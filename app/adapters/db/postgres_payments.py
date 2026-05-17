"""Pool PostgreSQL DEDICADO ao domínio `payments` (Empreiteiras-WF).

Por que pool separado?

  * Isolamento de carga: o pool genérico (`postgres.get_pool`) atende endpoints
    interativos (Radar/Raio-X). Cargas batch de payments (XLSX 869k linhas,
    MSRV5 3.1M linhas, extração PDF) podem segurar conexões por minutos. Sem
    pool dedicado, essas cargas roubam slots e degradam o p95 dos endpoints
    existentes — exatamente o que o gate de Fase 0 mede (k6 com 10 uploads
    paralelos vs SLO p95).
  * Tuning específico: `payments_pool_command_timeout` é mais alto (60s vs 30s)
    porque batches lentos não podem matar com timeout intermediário.
  * Observabilidade: spans/métricas marcados com `domain=payments` (ver
    `app/adapters/observability/composite_tracer.py`) ficam atribuíveis sem
    correlação por query.

API:
    get_payments_pool() -> asyncpg.Pool
    connect_payments() -> async ctx manager
    close_payments_pool() -> None
    init_payments_schema() -> None       # aplica migrations/*.sql idempotente

Implementação: copia a estrutura de `postgres.py` (codecs JSONB/timestamptz,
advisory lock no bootstrap) mas com pool independente. NÃO compartilha
conexões com o pool genérico — propósito explícito de isolamento.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any

import asyncpg

from app.config import get_settings

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


def _decode_timestamptz(value: str) -> _dt.datetime:
    parsed = _dt.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return parsed


def _encode_timestamptz(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.isoformat()
    return str(value)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Configura conexão recém-criada do pool payments.

    Idêntico ao `postgres._init_connection` (codecs JSONB/timestamptz +
    TIMEZONE=UTC), com adição de `SET search_path` apontando para o schema
    `payments` primeiro — queries não-qualificadas resolvem por aqui sem
    risco de colidir com tabelas do schema `public`.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: json.dumps(v, ensure_ascii=False, default=str) if v is not None else None,
        decoder=lambda v: json.loads(v) if v else None,
        schema="pg_catalog",
        format="text",
    )
    await conn.set_type_codec(
        "json",
        encoder=lambda v: json.dumps(v, ensure_ascii=False, default=str) if v is not None else None,
        decoder=lambda v: json.loads(v) if v else None,
        schema="pg_catalog",
        format="text",
    )
    await conn.set_type_codec(
        "timestamptz",
        encoder=_encode_timestamptz,
        decoder=_decode_timestamptz,
        schema="pg_catalog",
        format="text",
    )
    await conn.execute("SET TIME ZONE 'UTC'")
    # search_path: payments primeiro, public como fallback (FKs cross-schema
    # como `REFERENCES public.users(id)` ainda funcionam).
    await conn.execute("SET search_path TO payments, public")


async def get_payments_pool() -> asyncpg.Pool:
    """Retorna pool dedicado de payments. Inicializa sob lock se ainda não existir."""
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is not None:
            return _pool
        s = get_settings()
        _pool = await asyncpg.create_pool(
            dsn=s.pg_dsn,                                  # mesmo banco do generic
            min_size=s.payments_pool_min_size,
            max_size=s.payments_pool_max_size,
            max_inactive_connection_lifetime=s.payments_pool_max_inactive_connection_lifetime,
            command_timeout=s.payments_pool_command_timeout,
            statement_cache_size=1024,
            init=_init_connection,
        )
        return _pool


def connect_payments():
    """Async context manager — pega conexão do pool payments."""
    return _PaymentsConnectionContext()


class _PaymentsConnectionContext:
    __slots__ = ("_conn", "_acquire_cm")

    def __init__(self) -> None:
        self._conn: asyncpg.Connection | None = None
        self._acquire_cm = None

    async def __aenter__(self) -> asyncpg.Connection:
        p = await get_payments_pool()
        self._acquire_cm = p.acquire()
        self._conn = await self._acquire_cm.__aenter__()
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        if self._acquire_cm is not None:
            await self._acquire_cm.__aexit__(exc_type, exc, tb)


async def close_payments_pool() -> None:
    """Shutdown gracioso do pool payments. Idempotente."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

_MIGRATION_FILE_RE = re.compile(r"^(\d{3,})_.*\.sql$")


def _discover_migrations() -> list[Path]:
    """Lista arquivos `NNN_*.sql` em migrations/ ordenados por número."""
    if not _MIGRATIONS_DIR.is_dir():
        return []
    files = []
    for p in _MIGRATIONS_DIR.iterdir():
        if not p.is_file():
            continue
        m = _MIGRATION_FILE_RE.match(p.name)
        if m:
            files.append((int(m.group(1)), p))
    files.sort(key=lambda t: t[0])
    return [p for _, p in files]


async def init_payments_schema() -> None:
    """Aplica todas as migrations em ordem, idempotente.

    Idempotência via `CREATE ... IF NOT EXISTS` em cada DDL. O lock advisory
    impede que dois workers apliquem migrations concorrentemente.

    Chamado no startup do app (`app/main.py`) após `init_db()` do schema
    `public`. Cada migration roda em transação própria — se uma falha, as
    anteriores ficam comitadas.
    """
    migrations = _discover_migrations()
    if not migrations:
        return

    p = await get_payments_pool()
    async with p.acquire() as conn:
        _LOCK_KEY = 9000_000_001  # diferente do _INIT_DB_LOCK_KEY do postgres.py
        await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
        try:
            for migration_path in migrations:
                sql = migration_path.read_text(encoding="utf-8")
                # Pula migration vazia (apenas comentários)
                content = "\n".join(
                    ln for ln in sql.splitlines() if not ln.strip().startswith("--")
                ).strip()
                if not content:
                    continue
                async with conn.transaction():
                    await conn.execute(sql)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)


__all__ = [
    "close_payments_pool",
    "connect_payments",
    "get_payments_pool",
    "init_payments_schema",
]

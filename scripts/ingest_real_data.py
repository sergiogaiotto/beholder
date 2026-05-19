"""Dispara ingestão das 8 projeções com arquivos reais de
`C:\\_PERSONAL\\beholder_data`.

Usa exatamente o mesmo caminho da UI (`PaymentsIngestionService.queue_upload`),
então cada projeção vira um `IngestionRun(PENDING)` + um job dramatiq na
fila `payments_default`. O worker (`docker compose ... worker`) consome.

Ordem de enfileiramento: pequenos primeiro (feedback rápido), grandes
depois. O worker processa concorrentemente até `WORKER_THREADS_PER_PROCESS`
(default 4 no docker-compose.dev).

Idempotência: cada run cria um IngestionRun novo. SHA-256 do upload é
gravado no run mas o loader não pula uploads duplicados — se rodar 2x,
duplica dados. Cuidado.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.services.payments.ingestion_service import PaymentsIngestionService


# BEHOLDER_DATA_DIR permite rodar tanto no host (C:\_PERSONAL\beholder_data)
# quanto dentro do container (/data, montado via `docker cp`).
import os

DATA = Path(os.getenv("BEHOLDER_DATA_DIR", r"C:\_PERSONAL\beholder_data"))

# (projection_key, arquivo) — pares ordenados do menor pro maior pra
# que erros em projeção pequena sejam vistos antes de cargas longas.
PLAN: tuple[tuple[str, str], ...] = (
    ("supplier_bridge", "Contratos - Empreteiras.xlsx"),
    ("ekko", "EKKO - SAP (Extração pedidos).MHTML.xlsx"),
    ("esll", "ESLL - EXTRAÇÃO Nº DE PACOTES - LPU_VALORES.xlsx"),
    ("cost_center", "Analitico_Empreiteiras_WF1_WF2_TOTAL_2025 2.txt.xlsx"),
    ("gc", "Contratos - Empreteiras.xlsx"),
    ("ekpo", "EKPO - SAP (Extração pedidos).MHTML.xlsx"),
    ("wf_payment", "Analitico_Empreiteiras_WF1_WF2_TOTAL_2025 2.txt.xlsx"),
    ("msrv5", "MSRV5 - EXTRAÇÃO LPU.txt"),
)


async def _resolve_user_id(username: str) -> UUID:
    repo = PgUserRepository()
    user = await repo.get_by_username(username)
    if user is None:
        raise SystemExit(f"usuário não encontrado: {username}")
    return user.id


async def main() -> int:
    user_id = await _resolve_user_id("sergio.gaiotto")
    svc = PaymentsIngestionService()

    print(f"\n=== Enfileirando {len(PLAN)} cargas como user_id={user_id} ===\n")

    for projection_key, filename in PLAN:
        path = DATA / filename
        if not path.exists():
            print(f"[SKIP] {projection_key:18s} arquivo não existe: {path}", file=sys.stderr)
            continue

        data = path.read_bytes()
        size_mb = len(data) / 1024 / 1024
        try:
            run_id = await svc.queue_upload(
                file_bytes=data,
                filename=path.name,
                projection_name=projection_key,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {projection_key:18s} {exc}", file=sys.stderr)
            continue

        print(f"[QUEUED] {projection_key:18s} run={run_id} size={size_mb:6.1f} MB file={path.name}")

    print(
        "\n[OK] todos enfileirados. Acompanhe em "
        "http://localhost:8100/payments/empreiteiras-wf/ingestao"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

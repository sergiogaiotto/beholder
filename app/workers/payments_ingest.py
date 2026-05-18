"""Actor dramatiq de ingestão de XLSX/MSRV5 (Fase 3.5).

Recebe um run pré-criado (PENDING) + storage_key no DocumentStore + projection
name e roda o `load_source_by_path` no processo worker. Estratégia evita:
  - Timeout HTTP em cargas longas (MSRV5 leva ~9min na dev box).
  - Bloqueio do uvicorn (carga é CPU + IO bound — segura outras requisições).

Trade-off: o user precisa do worker dramatiq rodando (`dramatiq app.workers`).
docker-compose.dev.yml já levanta o serviço — em prod, scripts/deploy garantem.

Idempotência: actor é fire-and-forget. Se falhar, retries do dramatiq tentam
até max_retries=2 com backoff exponencial. Cada retry chama load_source
de novo — o loader é idempotente desde que existing_run_id seja respeitado
(reusa o run já criado).

Limpeza: após sucesso/falha, NÃO deletamos o arquivo do DocumentStore.
Operador pode reprocessar manualmente via CLI, e a chave funciona como
audit trail físico do upload (cruzar com IngestionRun.metadata).
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from uuid import UUID

import dramatiq

from app.adapters.db.repositories.payments import PgIngestionRunRepository
from app.adapters.storage.factory import get_document_store
from app.core.services.payments.ingestion.loader import load_source_by_path

logger = logging.getLogger(__name__)


@dramatiq.actor(
    queue_name="payments_default",
    max_retries=2,
    min_backoff=5_000,
    max_backoff=60_000,
    time_limit=900_000,  # 15 min — cobre MSRV5 (~9min observado em dev).
)
def ingest_source(
    run_id: str,
    storage_key: str,
    projection_name: str,
    triggered_by_user_id: str | None = None,
) -> None:
    """Roda 1 carga.

    Args:
      run_id: UUID do IngestionRun PENDING pré-criado pelo service.
      storage_key: chave do DocumentStore com o upload (XLSX ou TXT).
      projection_name: nome do YAML (`wf_payment`, `msrv5`, ...).
      triggered_by_user_id: opcional — UUID do usuário que clicou Upload.

    Returns:
      None (fire-and-forget). Resultados ficam em `payments.ingestion_run`.
    """
    asyncio.run(
        _run_ingestion(
            run_id=run_id,
            storage_key=storage_key,
            projection_name=projection_name,
            triggered_by_user_id=triggered_by_user_id,
        )
    )


async def _run_ingestion(
    *,
    run_id: str,
    storage_key: str,
    projection_name: str,
    triggered_by_user_id: str | None,
) -> None:
    """Lado async do actor — DocumentStore + asyncpg são async-native."""
    store = get_document_store()
    runs_repo = PgIngestionRunRepository()
    run_uuid = UUID(run_id)
    user_uuid = UUID(triggered_by_user_id) if triggered_by_user_id else None

    # 1. Baixa o upload pra path temp local. `load_source_by_path` quer Path,
    #    e o parser openpyxl precisa seekable — então materializamos em disco.
    try:
        payload = await store.get(storage_key)
    except FileNotFoundError as exc:
        await runs_repo.mark_failed(
            run_uuid,
            error_message=f"upload sumiu do DocumentStore: {storage_key!r}",
        )
        logger.exception("storage_key não encontrada: %s", storage_key)
        raise exc

    # Mantém a extensão original — alguns parsers (openpyxl) chiam sem .xlsx.
    suffix = Path(storage_key).suffix or ".bin"
    with tempfile.NamedTemporaryFile(
        prefix="ingest_", suffix=suffix, delete=False
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)

    # 2. Roda a carga. Loader pega o run existente via existing_run_id,
    #    marca running, processa, marca completed/failed.
    try:
        result = await load_source_by_path(
            tmp_path,
            projection_name,
            triggered_by_user_id=user_uuid,
            existing_run_id=run_uuid,
        )
        logger.info(
            "ingest_source OK run=%s rows_read=%d rows_inserted=%d",
            run_id, result.rows_read, result.rows_inserted,
        )
    except Exception as exc:
        # `load_source` já chamou mark_failed antes de propagar — mas log aqui
        # também para o operador ver no log do worker.
        logger.exception("ingest_source FAILED run=%s: %s", run_id, exc)
        raise
    finally:
        # Cleanup: deletar o temp local SEMPRE. Storage do DocumentStore fica
        # (é o audit trail físico do upload).
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            logger.warning("falha ao limpar temp %s — ignorando", tmp_path)

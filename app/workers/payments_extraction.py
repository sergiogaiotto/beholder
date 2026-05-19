"""Actor dramatiq de extração R7 (Fase 4 — PDF Extraction).

Recebe `job_id` de um ExtractionJob pendente e chama o pipeline. O actor
fica vazio de lógica de extração; toda a inteligência está em
`PaymentsExtractionService.process()` — facilita reuso via CLI/REPL e
isolamento de tests.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import dramatiq

logger = logging.getLogger(__name__)


@dramatiq.actor(
    queue_name="payments_default",
    max_retries=2,
    min_backoff=10_000,
    max_backoff=120_000,
    time_limit=180_000,  # 3 min — extração tipicamente <60s; folga pra LLM lento
)
def extract_pdf(job_id: str) -> None:
    """Roda 1 extração de PDF.

    Args:
      job_id: UUID do ExtractionJob (status='pending' criado pelo service).

    Returns:
      None — fire-and-forget. Resultados ficam em `payments.extraction_job`.
    """
    asyncio.run(_run(job_id))


async def _run(job_id_str: str) -> None:
    from app.core.services.payments.extraction.service import (
        PaymentsExtractionService,
    )

    # Em prod, instanciar com Maritaca client. Aqui usa default (Mock) —
    # mantemos isolado: prod ativa via env config no service constructor.
    svc = PaymentsExtractionService()
    await svc.process(UUID(job_id_str))

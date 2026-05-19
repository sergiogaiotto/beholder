"""Dispara extração só dos PDFs que AINDA não foram processados.

Útil quando o worker caiu no meio do batch — relê extraction_job pra
montar a lista de "já feito" (status='review'/'approved') e enfileira
só os que faltam.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.services.payments.extraction.service import (
    PaymentsExtractionService,
)


PDF_DIR = Path(os.getenv(
    "BEHOLDER_DATA_DIR",
    r"C:\_PERSONAL\beholder_data",
)) / "CONTRATOS_PDF"


async def _resolve_user_id(username: str) -> UUID:
    repo = PgUserRepository()
    user = await repo.get_by_username(username)
    if user is None:
        raise SystemExit(f"usuário não encontrado: {username}")
    return user.id


async def _already_done() -> set[str]:
    """Filenames já processados (review/approved/extracting)."""
    async with connect_payments() as c:
        rows = await c.fetch(
            "SELECT pdf_filename FROM payments.extraction_job "
            "WHERE status IN ('review','approved','extracting')"
        )
    return {r["pdf_filename"] for r in rows}


async def main() -> int:
    if not PDF_DIR.is_dir():
        raise SystemExit(f"pasta de PDFs não existe: {PDF_DIR}")

    user_id = await _resolve_user_id("sergio.gaiotto")
    svc = PaymentsExtractionService()
    done = await _already_done()
    print(f"\n=== Já processados (skip): {len(done)} ===\n")

    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    print(f"=== Total PDFs no disco: {len(pdfs)} ===\n")

    queued = 0
    skipped = 0
    failed = 0
    for pdf in pdfs:
        if pdf.name in done:
            skipped += 1
            continue
        try:
            data = pdf.read_bytes()
            job_id = await svc.queue_upload(
                pdf_bytes=data,
                filename=pdf.name,
                uploaded_by_id=user_id,
            )
            print(f"[QUEUED] {pdf.name:75s} job={job_id}")
            queued += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL]   {pdf.name:75s} {exc}", file=sys.stderr)
            failed += 1

    print(f"\n[OK] queued={queued} skipped={skipped} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

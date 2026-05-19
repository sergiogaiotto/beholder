"""Dispara extração dos 60 PDFs reais em CONTRATOS_PDF/.

Usa o mesmo caminho da UI (`PaymentsExtractionService.queue_upload`),
que salva o PDF no DocumentStore, cria `ExtractionJob(PENDING)` e
despacha o actor `extract_pdf`. O worker dramatiq chama Maritaca real
(se MARITACA_API_KEY está setada — sempre está no docker-compose.dev).

Tempo esperado: ~17s/PDF (Pré-C). Worker tem 4 threads → 60 PDFs em
batches de 4 → ~4-5 min total. Custo: ~60 × R$0,024 = R$ 1,44 (sem LPU).

Acompanhe em `/payments/empreiteiras-wf/contratos/extracao` ou via SQL:

    SELECT status, count(*) FROM payments.extraction_job GROUP BY 1;

Após status='review' a controladoria edita campos via UI e aprova.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

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


async def main() -> int:
    if not PDF_DIR.is_dir():
        raise SystemExit(f"pasta de PDFs não existe: {PDF_DIR}")

    user_id = await _resolve_user_id("sergio.gaiotto")
    svc = PaymentsExtractionService()

    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    print(f"\n=== Enfileirando {len(pdfs)} PDFs como user_id={user_id} ===\n")

    queued = 0
    skipped = 0
    failed = 0
    for pdf in pdfs:
        try:
            data = pdf.read_bytes()
            job_id = await svc.queue_upload(
                pdf_bytes=data,
                filename=pdf.name,
                uploaded_by_id=user_id,
            )
            print(f"[QUEUED] {pdf.name:75s} job={job_id}  size={len(data)/1024:6.0f} KB")
            queued += 1
        except ValueError as exc:
            print(f"[SKIP]   {pdf.name:75s} {exc}", file=sys.stderr)
            skipped += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL]   {pdf.name:75s} {exc}", file=sys.stderr)
            failed += 1

    print(
        f"\n[OK] queued={queued} skipped={skipped} failed={failed}. "
        f"Acompanhe em http://localhost:8100/payments/empreiteiras-wf/contratos/extracao"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

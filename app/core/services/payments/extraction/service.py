"""PaymentsExtractionService — orquestra upload/extração de PDFs de contrato (Fase 4).

Fluxo upload (ui-driven):

  1. UI POSTa PDF + uploaded_by → service.queue_upload()
  2. Service salva no DocumentStore, cria ExtractionJob(status='pending'),
     despacha actor dramatiq
  3. Actor (em payments_extraction.py) chama service.process(job_id)
  4. `process`: baixa PDF do storage → texto → LLM → set_results(status='review')
  5. UI HITL mostra resultados, controladoria aprova → service.approve(job_id, edited)
  6. Approve cria ContractMaster + ContractVersion + (Fase 4.x) LPUItem

Cliente LLM é injetado — `MockExtractionClient` pra tests, Maritaca real
em prod (config a definir no Bloco B se preciso).
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.payments.extraction_repo import (
    PgExtractionJobRepository,
)
from app.adapters.storage.factory import get_document_store
from app.core.domain.payments import ExtractionJob, ExtractionStatus
from app.core.services.payments.extraction._client import (
    LLMExtractionClient,
    MockExtractionClient,
)
from app.core.services.payments.extraction.schemas import (
    ExtractedContractFields,
)

logger = logging.getLogger(__name__)


def _storage_key_for_pdf(job_id: UUID, filename: str) -> str:
    """Convenção: payments/contracts/<job_id>/<filename>."""
    return f"payments/contracts/{job_id}/{filename}"


def _pdf_to_text(pdf_bytes: bytes) -> str:
    """PDF binário → texto plain. Usa pdfplumber (já é dep da Pré-C);
    docling pode substituir depois quando processarmos tabelas LPU.

    Em caso de erro, devolve string vazia — pipeline detecta e marca o job
    como failed com mensagem clara em vez de propagar exception."""
    try:
        import pdfplumber  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pdfplumber não instalado — extração impossível")
        return ""

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = Path(tmp.name)
        try:
            with pdfplumber.open(str(tmp_path)) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
                return "\n\n".join(pages)
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logger.exception("falha em pdfplumber")
        return ""


class PaymentsExtractionService:
    """Use case: extração de PDFs + workflow HITL."""

    def __init__(
        self,
        *,
        jobs_repo: PgExtractionJobRepository | None = None,
        document_store=None,
        llm_client: LLMExtractionClient | None = None,
    ):
        self.jobs_repo = jobs_repo or PgExtractionJobRepository()
        self.document_store = document_store or get_document_store()
        # Default mock — caller em produção injeta MaritacaExtractionClient.
        self.llm_client = llm_client or MockExtractionClient()

    # =================================================== Upload + queue

    async def queue_upload(
        self,
        *,
        pdf_bytes: bytes,
        filename: str,
        uploaded_by_id: UUID,
    ) -> UUID:
        """Salva PDF no storage, cria job(PENDING) e despacha actor.
        Retorna o `job_id` para a UI fazer polling."""
        if not pdf_bytes:
            raise ValueError("PDF vazio (0 bytes)")
        if not filename.lower().endswith(".pdf"):
            raise ValueError(f"filename precisa terminar em .pdf: {filename!r}")

        job = ExtractionJob(
            pdf_storage_key="",  # preenchido após put no DocStore
            pdf_filename=filename,
            pdf_size_bytes=len(pdf_bytes),
            status=ExtractionStatus.PENDING,
            uploaded_by_id=uploaded_by_id,
        )
        storage_key = _storage_key_for_pdf(job.id, filename)
        job.pdf_storage_key = storage_key

        await self.document_store.put(
            storage_key, pdf_bytes, content_type="application/pdf",
        )
        await self.jobs_repo.create(job)

        # Despacha o actor. Import tardio evita ciclo de importação.
        from app.workers.payments_extraction import extract_pdf

        extract_pdf.send(job_id=str(job.id))
        return job.id

    # ===================================================== Process (worker)

    async def process(self, job_id: UUID) -> None:
        """Pipeline: storage → pdf_text → LLM → set_results.
        Chamado pelo actor dramatiq."""
        job = await self.jobs_repo.get(job_id)
        if job is None:
            raise ValueError(f"job {job_id} não encontrado")

        await self.jobs_repo.update_status(job_id, status=ExtractionStatus.EXTRACTING)

        try:
            pdf_bytes = await self.document_store.get(job.pdf_storage_key)
            pdf_text = _pdf_to_text(pdf_bytes)
            if not pdf_text:
                await self.jobs_repo.update_status(
                    job_id, status=ExtractionStatus.FAILED,
                    error_message="pdf_to_text retornou vazio",
                )
                return

            result = await self.llm_client.extract(
                pdf_text=pdf_text, pdf_filename=job.pdf_filename,
            )
            # Pydantic → dict pra JSONB.
            extracted = result.fields.model_dump(mode="json")
            confidence = result.fields.confidence_per_field()

            await self.jobs_repo.set_results(
                job_id,
                extracted_fields=extracted,
                confidence_per_field=confidence,
                cost_brl=result.cost_brl,
                llm_model_used=result.llm_model_used,
            )
            logger.info(
                "extract_pdf OK job=%s model=%s cost=R$%.4f",
                job_id, result.llm_model_used, float(result.cost_brl),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("extract_pdf failed job=%s: %s", job_id, exc)
            await self.jobs_repo.update_status(
                job_id, status=ExtractionStatus.FAILED,
                error_message=repr(exc)[:500],
            )

    # ===================================================== Listings

    async def list_recent_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Histórico de extrações pra UI — mais recentes primeiro."""
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT
                    ej.id, ej.pdf_filename, ej.status, ej.cost_brl,
                    ej.created_at, ej.extraction_finished_at,
                    ej.error_message, ej.llm_model_used,
                    u.username AS uploaded_by_username
                FROM payments.extraction_job ej
                LEFT JOIN users u ON u.id = ej.uploaded_by_id
                ORDER BY ej.created_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [self._serialize_job(r) for r in rows]

    async def get_job_detail(self, job_id: UUID) -> dict[str, Any] | None:
        """Detalhe completo de 1 job pra tela HITL."""
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT
                    ej.*, u.username AS uploaded_by_username
                FROM payments.extraction_job ej
                LEFT JOIN users u ON u.id = ej.uploaded_by_id
                WHERE ej.id = $1
                """,
                job_id,
            )
            if row is None:
                return None

        import json
        def _parse(v):
            if v is None:
                return {}
            if isinstance(v, (dict, list)):
                return v
            return json.loads(v)

        return {
            "id": str(row["id"]),
            "pdf_filename": row["pdf_filename"],
            "pdf_storage_key": row["pdf_storage_key"],
            "pdf_size_bytes": int(row["pdf_size_bytes"] or 0),
            "pdf_pages": row["pdf_pages"],
            "status": row["status"],
            "extracted_fields": _parse(row["extracted_fields"]),
            "confidence_per_field": _parse(row["confidence_per_field"]),
            "cost_brl": float(row["cost_brl"] or 0),
            "llm_model_used": row["llm_model_used"],
            "error_message": row["error_message"],
            "uploaded_by_username": row["uploaded_by_username"],
            "created_at": row["created_at"],
            "extraction_finished_at": row["extraction_finished_at"],
        }

    @staticmethod
    def _serialize_job(row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "pdf_filename": row["pdf_filename"],
            "status": row["status"],
            "status_label": {
                "pending":    "Aguardando",
                "extracting": "Extraindo",
                "review":     "Revisão",
                "approved":   "Aprovado",
                "failed":     "Falhou",
            }.get(row["status"], row["status"]),
            "cost_brl_fmt": f"R$ {float(row['cost_brl'] or 0):.4f}",
            "llm_model_used": row["llm_model_used"] or "—",
            "created_at": row["created_at"],
            "created_at_fmt": row["created_at"].strftime("%d/%m/%Y %H:%M"),
            "finished_at_fmt": (
                row["extraction_finished_at"].strftime("%d/%m/%Y %H:%M")
                if row["extraction_finished_at"]
                else "—"
            ),
            "uploaded_by_username": row["uploaded_by_username"] or "—",
            "error_message": row["error_message"],
        }

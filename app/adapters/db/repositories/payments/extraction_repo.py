"""Implementação asyncpg de ExtractionJobRepository (Fase 4)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.payments._helpers import record_to_dict
from app.core.domain.payments import ExtractionJob, ExtractionStatus
from app.core.ports.payments.repositories import ExtractionJobRepository


class PgExtractionJobRepository(ExtractionJobRepository):

    async def create(self, job: ExtractionJob) -> ExtractionJob:
        async with connect_payments() as c:
            await c.execute(
                """
                INSERT INTO payments.extraction_job (
                    id, contract_master_id, pdf_storage_key, pdf_filename,
                    pdf_size_bytes, pdf_pages, status,
                    extraction_started_at, extraction_finished_at,
                    extracted_fields, confidence_per_field, llm_model_used,
                    cost_brl, error_message, uploaded_by_id, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15, $16
                )
                """,
                job.id, job.contract_master_id, job.pdf_storage_key,
                job.pdf_filename, job.pdf_size_bytes, job.pdf_pages,
                job.status.value, job.extraction_started_at,
                job.extraction_finished_at, job.extracted_fields,
                job.confidence_per_field, job.llm_model_used, job.cost_brl,
                job.error_message, job.uploaded_by_id, job.created_at,
            )
            return job

    async def get(self, job_id: UUID) -> ExtractionJob | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.extraction_job WHERE id = $1", job_id
            )
            return ExtractionJob.model_validate(record_to_dict(row)) if row else None

    async def update_status(
        self,
        job_id: UUID,
        *,
        status: ExtractionStatus,
        error_message: str | None = None,
    ) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.extraction_job
                SET status = $1,
                    error_message = COALESCE($2, error_message)
                WHERE id = $3
                """,
                status.value, error_message, job_id,
            )

    async def set_results(
        self,
        job_id: UUID,
        *,
        extracted_fields: dict,
        confidence_per_field: dict,
        cost_brl: Decimal,
        llm_model_used: str,
    ) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.extraction_job
                SET extracted_fields = $1,
                    confidence_per_field = $2,
                    cost_brl = $3,
                    llm_model_used = $4,
                    extraction_finished_at = NOW(),
                    status = $5
                WHERE id = $6
                """,
                extracted_fields, confidence_per_field, cost_brl,
                llm_model_used, ExtractionStatus.REVIEW.value, job_id,
            )

    async def list_pending(self, *, limit: int = 50) -> list[ExtractionJob]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.extraction_job
                WHERE status = 'pending'
                ORDER BY created_at
                LIMIT $1
                """,
                limit,
            )
            return [ExtractionJob.model_validate(record_to_dict(r)) for r in rows]

    async def list_for_review(self, *, limit: int = 50) -> list[ExtractionJob]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.extraction_job
                WHERE status = 'review'
                ORDER BY extraction_finished_at DESC NULLS LAST
                LIMIT $1
                """,
                limit,
            )
            return [ExtractionJob.model_validate(record_to_dict(r)) for r in rows]

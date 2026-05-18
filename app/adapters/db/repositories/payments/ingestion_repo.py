"""Implementação asyncpg de IngestionRunRepository."""

from __future__ import annotations

from uuid import UUID

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.payments._helpers import record_to_dict
from app.core.domain.payments import IngestionRun, IngestionStatus
from app.core.ports.payments.repositories import IngestionRunRepository


class PgIngestionRunRepository(IngestionRunRepository):

    async def create(self, run: IngestionRun) -> IngestionRun:
        async with connect_payments() as c:
            await c.execute(
                """
                INSERT INTO payments.ingestion_run (
                    id, source_type, source_filename, source_sha256,
                    source_size_bytes, target_table, status,
                    rows_read, rows_inserted, rows_skipped, rows_failed,
                    started_at, finished_at, error_message,
                    triggered_by_user_id, metadata
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7,
                    $8, $9, $10, $11, $12, $13, $14, $15, $16
                )
                """,
                run.id, run.source_type, run.source_filename, run.source_sha256,
                run.source_size_bytes, run.target_table, run.status.value,
                run.rows_read, run.rows_inserted, run.rows_skipped, run.rows_failed,
                run.started_at, run.finished_at, run.error_message,
                run.triggered_by_user_id, run.metadata,
            )
            return run

    async def get(self, run_id: UUID) -> IngestionRun | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.ingestion_run WHERE id = $1", run_id
            )
            return IngestionRun.model_validate(record_to_dict(row)) if row else None

    async def mark_running(self, run_id: UUID) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.ingestion_run
                SET status = $1
                WHERE id = $2
                """,
                IngestionStatus.RUNNING.value, run_id,
            )

    async def mark_completed(
        self,
        run_id: UUID,
        *,
        rows_read: int,
        rows_inserted: int,
        rows_skipped: int = 0,
        rows_failed: int = 0,
    ) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.ingestion_run
                SET status = $1,
                    finished_at = NOW(),
                    rows_read = $2,
                    rows_inserted = $3,
                    rows_skipped = $4,
                    rows_failed = $5
                WHERE id = $6
                """,
                IngestionStatus.COMPLETED.value,
                rows_read, rows_inserted, rows_skipped, rows_failed,
                run_id,
            )

    async def mark_failed(self, run_id: UUID, *, error_message: str) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.ingestion_run
                SET status = $1,
                    finished_at = NOW(),
                    error_message = $2
                WHERE id = $3
                """,
                IngestionStatus.FAILED.value, error_message, run_id,
            )

    async def list_recent(self, *, limit: int = 50) -> list[IngestionRun]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.ingestion_run
                ORDER BY started_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [IngestionRun.model_validate(record_to_dict(r)) for r in rows]

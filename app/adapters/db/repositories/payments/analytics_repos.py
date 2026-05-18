"""Implementação asyncpg dos 2 repos analytics R7:
PgAnalyticDetector, PgAnalyticFinding.
"""

from __future__ import annotations

from uuid import UUID

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.payments._helpers import record_to_dict
from app.core.domain.payments import (
    AnalyticDetector,
    AnalyticFinding,
    FindingStatus,
)
from app.core.ports.payments.repositories import (
    AnalyticDetectorRepository,
    AnalyticFindingRepository,
)


# ---------------------------------------------------------------------------
# AnalyticDetector (catálogo R7)
# ---------------------------------------------------------------------------


class PgAnalyticDetectorRepository(AnalyticDetectorRepository):

    async def list_active(self) -> list[AnalyticDetector]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.analytic_detector
                WHERE is_active = TRUE
                ORDER BY code
                """
            )
            return [AnalyticDetector.model_validate(record_to_dict(r)) for r in rows]

    async def list_all(self) -> list[AnalyticDetector]:
        async with connect_payments() as c:
            rows = await c.fetch(
                "SELECT * FROM payments.analytic_detector ORDER BY code"
            )
            return [AnalyticDetector.model_validate(record_to_dict(r)) for r in rows]

    async def get(self, detector_id: UUID) -> AnalyticDetector | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.analytic_detector WHERE id = $1", detector_id
            )
            return (
                AnalyticDetector.model_validate(record_to_dict(row)) if row else None
            )

    async def get_by_code(self, code: str) -> AnalyticDetector | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.analytic_detector WHERE code = $1", code
            )
            return (
                AnalyticDetector.model_validate(record_to_dict(row)) if row else None
            )

    async def save(self, detector: AnalyticDetector) -> AnalyticDetector:
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                INSERT INTO payments.analytic_detector (
                    id, code, name, description, technique, severity,
                    is_active, threshold_params, python_handler, version,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
                )
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    technique = EXCLUDED.technique,
                    severity = EXCLUDED.severity,
                    is_active = EXCLUDED.is_active,
                    threshold_params = EXCLUDED.threshold_params,
                    python_handler = EXCLUDED.python_handler,
                    version = EXCLUDED.version,
                    updated_at = NOW()
                RETURNING *
                """,
                detector.id, detector.code, detector.name, detector.description,
                detector.technique.value, detector.severity.value,
                detector.is_active, detector.threshold_params,
                detector.python_handler, detector.version,
                detector.created_at, detector.updated_at,
            )
            return AnalyticDetector.model_validate(record_to_dict(row))

    async def set_active(self, detector_id: UUID, active: bool) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.analytic_detector
                SET is_active = $1, updated_at = NOW()
                WHERE id = $2
                """,
                active, detector_id,
            )

    async def count(self) -> int:
        async with connect_payments() as c:
            n = await c.fetchval("SELECT COUNT(*) FROM payments.analytic_detector")
            return int(n or 0)


# ---------------------------------------------------------------------------
# AnalyticFinding
# ---------------------------------------------------------------------------


def _af_tuple(f: AnalyticFinding) -> tuple:
    return (
        f.id, f.detector_id, f.detector_code, f.severity.value,
        f.wf_payment_id, f.wf_payment_data_pedido, f.supplier_id,
        f.score, f.expected_range, f.actual_value, f.evidence_payment_ids,
        f.status.value,
        f.analyst_id, f.decision_reason, f.decided_by_id, f.decided_at,
        f.detected_at,
    )


_AF_INSERT_SQL = """
    INSERT INTO payments.analytic_finding (
        id, detector_id, detector_code, severity,
        wf_payment_id, wf_payment_data_pedido, supplier_id,
        score, expected_range, actual_value, evidence_payment_ids,
        status,
        analyst_id, decision_reason, decided_by_id, decided_at,
        detected_at
    ) VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
        $13, $14, $15, $16, $17
    )
"""


class PgAnalyticFindingRepository(AnalyticFindingRepository):

    async def create(self, finding: AnalyticFinding) -> AnalyticFinding:
        async with connect_payments() as c:
            await c.execute(_AF_INSERT_SQL, *_af_tuple(finding))
            return finding

    async def bulk_insert(self, findings: list[AnalyticFinding]) -> int:
        if not findings:
            return 0
        async with connect_payments() as c:
            await c.executemany(_AF_INSERT_SQL, [_af_tuple(f) for f in findings])
        return len(findings)

    async def get(self, finding_id: UUID) -> AnalyticFinding | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.analytic_finding WHERE id = $1", finding_id
            )
            return AnalyticFinding.model_validate(record_to_dict(row)) if row else None

    async def update_status(
        self,
        finding_id: UUID,
        *,
        status: FindingStatus,
        analyst_id: UUID | None = None,
        decision_reason: str | None = None,
    ) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.analytic_finding
                SET status = $1,
                    analyst_id = COALESCE($2, analyst_id),
                    decision_reason = COALESCE($3, decision_reason),
                    decided_by_id = COALESCE($2, decided_by_id),
                    decided_at = NOW()
                WHERE id = $4
                """,
                status.value, analyst_id, decision_reason, finding_id,
            )

    async def list_inbox(
        self,
        *,
        status: FindingStatus = FindingStatus.OPEN,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AnalyticFinding]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.analytic_finding
                WHERE status = $1
                ORDER BY severity DESC, detected_at DESC
                LIMIT $2 OFFSET $3
                """,
                status.value, limit, offset,
            )
            return [AnalyticFinding.model_validate(record_to_dict(r)) for r in rows]

    async def count_open(self) -> int:
        async with connect_payments() as c:
            n = await c.fetchval(
                "SELECT COUNT(*) FROM payments.analytic_finding WHERE status = 'open'"
            )
            return int(n or 0)

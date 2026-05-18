"""Implementação asyncpg dos 3 repos do rules engine:
PgRuleDefinition, PgReconciliationRun, PgReconciliationFinding.
"""

from __future__ import annotations

from uuid import UUID

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.payments._helpers import record_to_dict
from app.core.domain.payments import (
    FindingStatus,
    ReconciliationFinding,
    ReconciliationRun,
    RuleDefinition,
    RunStatus,
)
from app.core.ports.payments.repositories import (
    ReconciliationFindingRepository,
    ReconciliationRunRepository,
    RuleDefinitionRepository,
)


# ---------------------------------------------------------------------------
# RuleDefinition (catálogo)
# ---------------------------------------------------------------------------


class PgRuleDefinitionRepository(RuleDefinitionRepository):

    async def list_active(self) -> list[RuleDefinition]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.rule_definition
                WHERE is_active = TRUE
                ORDER BY code
                """
            )
            return [RuleDefinition.model_validate(record_to_dict(r)) for r in rows]

    async def list_all(self) -> list[RuleDefinition]:
        async with connect_payments() as c:
            rows = await c.fetch(
                "SELECT * FROM payments.rule_definition ORDER BY code"
            )
            return [RuleDefinition.model_validate(record_to_dict(r)) for r in rows]

    async def get(self, rule_id: UUID) -> RuleDefinition | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.rule_definition WHERE id = $1", rule_id
            )
            return RuleDefinition.model_validate(record_to_dict(row)) if row else None

    async def get_by_code(self, code: str) -> RuleDefinition | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.rule_definition WHERE code = $1", code
            )
            return RuleDefinition.model_validate(record_to_dict(row)) if row else None

    async def save(self, rule: RuleDefinition) -> RuleDefinition:
        """Upsert por `code` — semântica de seed reidempotente."""
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                INSERT INTO payments.rule_definition (
                    id, code, name, description, severity, is_active,
                    threshold_params, engine_type, python_handler, version,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
                )
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    severity = EXCLUDED.severity,
                    is_active = EXCLUDED.is_active,
                    threshold_params = EXCLUDED.threshold_params,
                    engine_type = EXCLUDED.engine_type,
                    python_handler = EXCLUDED.python_handler,
                    version = EXCLUDED.version,
                    updated_at = NOW()
                RETURNING *
                """,
                rule.id, rule.code, rule.name, rule.description,
                rule.severity.value, rule.is_active, rule.threshold_params,
                rule.engine_type.value, rule.python_handler, rule.version,
                rule.created_at, rule.updated_at,
            )
            return RuleDefinition.model_validate(record_to_dict(row))

    async def set_active(self, rule_id: UUID, active: bool) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.rule_definition
                SET is_active = $1, updated_at = NOW()
                WHERE id = $2
                """,
                active, rule_id,
            )

    async def count(self) -> int:
        async with connect_payments() as c:
            n = await c.fetchval("SELECT COUNT(*) FROM payments.rule_definition")
            return int(n or 0)


# ---------------------------------------------------------------------------
# ReconciliationRun (workflow)
# ---------------------------------------------------------------------------


class PgReconciliationRunRepository(ReconciliationRunRepository):

    async def create(self, run: ReconciliationRun) -> ReconciliationRun:
        async with connect_payments() as c:
            await c.execute(
                """
                INSERT INTO payments.reconciliation_run (
                    id, triggered_by, triggered_by_user_id, rules_executed,
                    scope_filter, status, started_at, finished_at,
                    findings_created, error_message
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
                )
                """,
                run.id, run.triggered_by.value, run.triggered_by_user_id,
                run.rules_executed, run.scope_filter, run.status.value,
                run.started_at, run.finished_at, run.findings_created,
                run.error_message,
            )
            return run

    async def get(self, run_id: UUID) -> ReconciliationRun | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.reconciliation_run WHERE id = $1", run_id
            )
            return (
                ReconciliationRun.model_validate(record_to_dict(row)) if row else None
            )

    async def mark_completed(self, run_id: UUID, *, findings_created: int) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.reconciliation_run
                SET status = $1, finished_at = NOW(), findings_created = $2
                WHERE id = $3
                """,
                RunStatus.COMPLETED.value, findings_created, run_id,
            )

    async def mark_failed(self, run_id: UUID, *, error_message: str) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.reconciliation_run
                SET status = $1, finished_at = NOW(), error_message = $2
                WHERE id = $3
                """,
                RunStatus.FAILED.value, error_message, run_id,
            )

    async def list_recent(self, *, limit: int = 50) -> list[ReconciliationRun]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.reconciliation_run
                ORDER BY started_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [
                ReconciliationRun.model_validate(record_to_dict(r)) for r in rows
            ]


# ---------------------------------------------------------------------------
# ReconciliationFinding
# ---------------------------------------------------------------------------


def _finding_tuple(f: ReconciliationFinding) -> tuple:
    return (
        f.id, f.run_id, f.rule_id, f.rule_code, f.severity.value, f.status.value,
        f.purchase_order_documento, f.purchase_order_item,
        f.wf_payment_id, f.wf_payment_data_pedido,
        f.contract_master_id, f.contract_version_id, f.supplier_id,
        f.is_monitored_supplier,
        f.expected_value, f.actual_value, f.delta_pct, f.value_at_risk_brl,
        f.evidence_clause_ids, f.evidence_pages,
        f.analyst_id, f.decision_reason, f.decided_by_id, f.decided_at,
        f.detected_at,
    )


_FINDING_INSERT_SQL = """
    INSERT INTO payments.reconciliation_finding (
        id, run_id, rule_id, rule_code, severity, status,
        purchase_order_documento, purchase_order_item,
        wf_payment_id, wf_payment_data_pedido,
        contract_master_id, contract_version_id, supplier_id,
        is_monitored_supplier,
        expected_value, actual_value, delta_pct, value_at_risk_brl,
        evidence_clause_ids, evidence_pages,
        analyst_id, decision_reason, decided_by_id, decided_at,
        detected_at
    ) VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
        $11, $12, $13, $14, $15, $16, $17, $18,
        $19, $20, $21, $22, $23, $24, $25
    )
"""


class PgReconciliationFindingRepository(ReconciliationFindingRepository):

    async def create(self, finding: ReconciliationFinding) -> ReconciliationFinding:
        async with connect_payments() as c:
            await c.execute(_FINDING_INSERT_SQL, *_finding_tuple(finding))
            return finding

    async def bulk_insert(self, findings: list[ReconciliationFinding]) -> int:
        if not findings:
            return 0
        async with connect_payments() as c:
            await c.executemany(
                _FINDING_INSERT_SQL, [_finding_tuple(f) for f in findings]
            )
        return len(findings)

    async def get(self, finding_id: UUID) -> ReconciliationFinding | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.reconciliation_finding WHERE id = $1",
                finding_id,
            )
            return (
                ReconciliationFinding.model_validate(record_to_dict(row))
                if row else None
            )

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
                UPDATE payments.reconciliation_finding
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
        monitored_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReconciliationFinding]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.reconciliation_finding
                WHERE status = $1
                  AND ($2::bool IS FALSE OR is_monitored_supplier = TRUE)
                ORDER BY severity DESC, detected_at DESC
                LIMIT $3 OFFSET $4
                """,
                status.value, monitored_only, limit, offset,
            )
            return [
                ReconciliationFinding.model_validate(record_to_dict(r)) for r in rows
            ]

    async def count_open(self, *, monitored_only: bool = True) -> int:
        async with connect_payments() as c:
            n = await c.fetchval(
                """
                SELECT COUNT(*) FROM payments.reconciliation_finding
                WHERE status = 'open'
                  AND ($1::bool IS FALSE OR is_monitored_supplier = TRUE)
                """,
                monitored_only,
            )
            return int(n or 0)

"""Integration tests dos 3 repos do rules engine."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.adapters.db.repositories.payments import (
    PgReconciliationFindingRepository,
    PgReconciliationRunRepository,
    PgRuleDefinitionRepository,
)
from app.core.domain.payments import (
    EngineType,
    FindingStatus,
    ReconciliationFinding,
    ReconciliationRun,
    RuleDefinition,
    RunStatus,
    Severity,
    TriggeredBy,
)


# ---------- RuleDefinition ----------


async def test_rule_definition_seed_populated():
    """Migration 007 já populou 20 regras — list_active retorna isso."""
    repo = PgRuleDefinitionRepository()
    active = await repo.list_active()
    assert len(active) == 20
    codes = {r.code for r in active}
    assert "REGRA_1" in codes
    assert "REGRA_LPU" in codes
    assert "REGRA_6_9" in codes


async def test_rule_save_upsert_by_code():
    repo = PgRuleDefinitionRepository()
    rule = RuleDefinition(
        code="REGRA_TEST",
        name="test",
        description="d",
        severity=Severity.LOW,
        engine_type=EngineType.SQL_DETERMINISTIC,
        python_handler="x.y.z",
    )
    saved = await repo.save(rule)
    assert saved.code == "REGRA_TEST"

    # Atualizar campos sem mudar `code` → upsert
    rule.description = "updated"
    rule.severity = Severity.HIGH
    updated = await repo.save(rule)
    assert updated.description == "updated"
    assert updated.severity is Severity.HIGH

    # Não duplicou
    by_code = await repo.get_by_code("REGRA_TEST")
    assert by_code is not None
    assert by_code.severity is Severity.HIGH


async def test_rule_set_active_toggle():
    repo = PgRuleDefinitionRepository()
    rule = await repo.get_by_code("REGRA_1")
    assert rule.is_active is True

    await repo.set_active(rule.id, False)
    refreshed = await repo.get_by_code("REGRA_1")
    assert refreshed.is_active is False

    # Restaura para não vazar pros próximos tests do mesmo arquivo
    await repo.set_active(rule.id, True)


# ---------- ReconciliationRun ----------


async def test_recon_run_create_then_mark_completed():
    repo = PgReconciliationRunRepository()
    run = ReconciliationRun(
        triggered_by=TriggeredBy.MANUAL,
        rules_executed=["REGRA_1", "REGRA_2"],
        status=RunStatus.RUNNING,
    )
    await repo.create(run)

    await repo.mark_completed(run.id, findings_created=5)
    fetched = await repo.get(run.id)
    assert fetched.status is RunStatus.COMPLETED
    assert fetched.findings_created == 5
    assert fetched.finished_at is not None


async def test_recon_run_mark_failed_records_error():
    repo = PgReconciliationRunRepository()
    run = ReconciliationRun(
        triggered_by=TriggeredBy.SCHEDULED,
        rules_executed=["REGRA_3"],
        status=RunStatus.RUNNING,
    )
    await repo.create(run)

    await repo.mark_failed(run.id, error_message="contract_version not found")
    fetched = await repo.get(run.id)
    assert fetched.status is RunStatus.FAILED
    assert fetched.error_message == "contract_version not found"


# ---------- ReconciliationFinding ----------


async def _create_run() -> ReconciliationRun:
    repo = PgReconciliationRunRepository()
    run = ReconciliationRun(
        triggered_by=TriggeredBy.MANUAL,
        rules_executed=["REGRA_6_5"],
        status=RunStatus.RUNNING,
    )
    await repo.create(run)
    return run


async def test_finding_create_then_get():
    rules_repo = PgRuleDefinitionRepository()
    f_repo = PgReconciliationFindingRepository()
    run = await _create_run()

    rule = await rules_repo.get_by_code("REGRA_6_5")
    finding = ReconciliationFinding(
        run_id=run.id,
        rule_id=rule.id,
        rule_code=rule.code,
        severity=rule.severity,
        purchase_order_documento="4500000000",
        wf_payment_id=42,
        wf_payment_data_pedido=date(2025, 6, 1),
        expected_value={"valor": 100.0},
        actual_value={"valor": 110.0},
        delta_pct=10.0,
        value_at_risk_brl=Decimal("10.00"),
    )
    await f_repo.create(finding)
    fetched = await f_repo.get(finding.id)
    assert fetched is not None
    assert fetched.delta_pct == 10.0
    assert fetched.wf_payment_data_pedido == date(2025, 6, 1)
    assert fetched.value_at_risk_brl == Decimal("10.00")


async def test_finding_bulk_insert_e_inbox(test_user_id):
    rules_repo = PgRuleDefinitionRepository()
    f_repo = PgReconciliationFindingRepository()
    run = await _create_run()
    rule = await rules_repo.get_by_code("REGRA_6_5")

    findings = [
        ReconciliationFinding(
            run_id=run.id,
            rule_id=rule.id,
            rule_code=rule.code,
            severity=rule.severity,
            purchase_order_documento=f"4500{i:06}",
            expected_value={"v": i},
            actual_value={"v": i + 1},
            is_monitored_supplier=(i % 2 == 0),
        )
        for i in range(4)
    ]
    n = await f_repo.bulk_insert(findings)
    assert n == 4
    assert await f_repo.count_open(monitored_only=False) == 4
    assert await f_repo.count_open(monitored_only=True) == 2

    inbox = await f_repo.list_inbox(monitored_only=True)
    assert len(inbox) == 2


async def test_finding_update_status_records_analyst(test_user_id):
    rules_repo = PgRuleDefinitionRepository()
    f_repo = PgReconciliationFindingRepository()
    run = await _create_run()
    rule = await rules_repo.get_by_code("REGRA_6_5")

    f = ReconciliationFinding(
        run_id=run.id,
        rule_id=rule.id,
        rule_code=rule.code,
        severity=rule.severity,
        purchase_order_documento="4500000000",
        expected_value={"v": 1},
        actual_value={"v": 2},
    )
    await f_repo.create(f)

    await f_repo.update_status(
        f.id,
        status=FindingStatus.ACCEPTED_FP,
        analyst_id=test_user_id,
        decision_reason="False positive verified",
    )
    fetched = await f_repo.get(f.id)
    assert fetched.status is FindingStatus.ACCEPTED_FP
    assert fetched.analyst_id == test_user_id
    assert fetched.decision_reason == "False positive verified"
    assert fetched.decided_at is not None

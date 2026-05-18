"""Tests integrados do ReconciliationEngine.

Cada test usa o catálogo do seed (REGRA_1-LPU já no DB) ou um RuleDefinition
custom inserido via repo. Os handlers usados aqui são stubs registrados
direto no teste (registry é isolado pelo conftest).
"""

from __future__ import annotations

import pytest

from app.adapters.db.repositories.payments import (
    PgReconciliationFindingRepository,
    PgReconciliationRunRepository,
    PgRuleDefinitionRepository,
)
from app.core.domain.payments import (
    EngineType,
    FindingStatus,
    RunStatus,
    Severity,
    TriggeredBy,
)
from app.core.domain.payments import (
    RuleDefinition as RuleDefinitionDomain,
)
from app.core.services.payments.reconciliation_engine import ReconciliationEngine
from app.core.services.payments.rules import (
    RULES_REGISTRY,
    FindingDraft,
    register,
)


def _make_engine() -> ReconciliationEngine:
    return ReconciliationEngine(
        rule_repo=PgRuleDefinitionRepository(),
        run_repo=PgReconciliationRunRepository(),
        finding_repo=PgReconciliationFindingRepository(),
        batch_size=5,  # batch pequeno pra testar flush
    )


async def _register_test_rule(code: str) -> RuleDefinitionDomain:
    """Insere uma RuleDefinition fresh no catálogo (cleanup automático)."""
    rule = RuleDefinitionDomain(
        code=code,
        name="test rule",
        description="x",
        severity=Severity.MEDIUM,
        engine_type=EngineType.SQL_DETERMINISTIC,
        python_handler="tests.unused",
    )
    rules_repo = PgRuleDefinitionRepository()
    return await rules_repo.save(rule)


async def test_engine_run_no_findings_marks_completed():
    """Handler que não emite nada → run completed com findings_created=0."""
    rule = await _register_test_rule("REGRA_TEST_EMPTY")

    @register("REGRA_TEST_EMPTY")
    async def _empty_handler(ctx):
        return
        yield  # noqa — generator marker

    run = await _make_engine().run(["REGRA_TEST_EMPTY"])
    assert run.status is RunStatus.COMPLETED
    assert run.findings_created == 0
    assert run.finished_at is not None
    assert run.rules_executed == ["REGRA_TEST_EMPTY"]


async def test_engine_persists_findings_in_batches():
    """Handler que emite 12 drafts com batch_size=5 → 3 flushes (5+5+2)."""
    await _register_test_rule("REGRA_TEST_BATCH")

    @register("REGRA_TEST_BATCH")
    async def _h(ctx):
        for i in range(12):
            yield FindingDraft(
                rule_code="REGRA_TEST_BATCH",
                severity=Severity.MEDIUM,
                purchase_order_documento=f"PO-{i:04}",
                expected_value={"i": i},
                actual_value={"i": i + 1},
            )

    run = await _make_engine().run(["REGRA_TEST_BATCH"])
    assert run.findings_created == 12

    f_repo = PgReconciliationFindingRepository()
    count = await f_repo.count_open(monitored_only=True)
    assert count == 12


async def test_engine_marks_failed_on_handler_exception():
    """Handler que levanta → run marked failed + exception re-raised."""
    await _register_test_rule("REGRA_TEST_FAIL")

    @register("REGRA_TEST_FAIL")
    async def _h(ctx):
        yield FindingDraft(
            rule_code="REGRA_TEST_FAIL",
            severity=Severity.LOW,
            purchase_order_documento="X",
            expected_value={},
            actual_value={},
        )
        raise RuntimeError("handler boom")

    with pytest.raises(RuntimeError, match="boom"):
        await _make_engine().run(["REGRA_TEST_FAIL"])

    # Run foi marcado failed; mensagem persistida
    run_repo = PgReconciliationRunRepository()
    recent = await run_repo.list_recent(limit=5)
    failed = [r for r in recent if r.status is RunStatus.FAILED]
    assert len(failed) >= 1
    assert "boom" in failed[0].error_message


async def test_engine_unknown_rule_code_aborts():
    """rule_code que não existe no catálogo → ValueError ANTES de criar run."""
    engine = _make_engine()
    with pytest.raises(ValueError, match="não encontrados"):
        await engine.run(["REGRA_NAO_EXISTE"])


async def test_engine_missing_handler_marks_failed():
    """Rule existe no catálogo mas handler não está em RULES_REGISTRY."""
    await _register_test_rule("REGRA_TEST_NO_HANDLER")
    # Sem @register("REGRA_TEST_NO_HANDLER") → engine deve marcar failed

    with pytest.raises(ValueError, match="no handler registered"):
        await _make_engine().run(["REGRA_TEST_NO_HANDLER"])

    run_repo = PgReconciliationRunRepository()
    recent = await run_repo.list_recent(limit=5)
    failed = [r for r in recent if r.status is RunStatus.FAILED]
    assert len(failed) >= 1


async def test_engine_executes_multiple_rules_in_order():
    """rule_codes=[A, B] → A roda primeiro, B depois; findings_created soma."""
    await _register_test_rule("REGRA_TEST_A")
    await _register_test_rule("REGRA_TEST_B")
    call_order: list[str] = []

    @register("REGRA_TEST_A")
    async def _a(ctx):
        call_order.append("A")
        yield FindingDraft(
            rule_code="REGRA_TEST_A",
            severity=Severity.LOW,
            purchase_order_documento="X-A",
            expected_value={}, actual_value={},
        )

    @register("REGRA_TEST_B")
    async def _b(ctx):
        call_order.append("B")
        yield FindingDraft(
            rule_code="REGRA_TEST_B",
            severity=Severity.LOW,
            purchase_order_documento="X-B",
            expected_value={}, actual_value={},
        )
        yield FindingDraft(
            rule_code="REGRA_TEST_B",
            severity=Severity.LOW,
            purchase_order_documento="X-B2",
            expected_value={}, actual_value={},
        )

    run = await _make_engine().run(["REGRA_TEST_A", "REGRA_TEST_B"])
    assert run.findings_created == 3
    assert call_order == ["A", "B"]


async def test_engine_empty_rule_codes_raises():
    engine = _make_engine()
    with pytest.raises(ValueError, match="vazio"):
        await engine.run([])


async def test_engine_passes_threshold_params_via_context(test_user_id):
    """rule.threshold_params chega no handler via ctx.rule.threshold_params."""
    rules_repo = PgRuleDefinitionRepository()
    rule = RuleDefinitionDomain(
        code="REGRA_TEST_PARAMS",
        name="test", description="x",
        severity=Severity.MEDIUM,
        engine_type=EngineType.MATH_TOLERANCE,
        python_handler="x.y",
        threshold_params={"tolerance_pct": 2.5},
    )
    await rules_repo.save(rule)

    received_params = {}

    @register("REGRA_TEST_PARAMS")
    async def _h(ctx):
        received_params.update(ctx.rule.threshold_params)
        return
        yield

    run = await _make_engine().run(
        ["REGRA_TEST_PARAMS"],
        triggered_by=TriggeredBy.MANUAL,
        triggered_by_user_id=test_user_id,
    )
    assert run.triggered_by_user_id == test_user_id
    assert received_params == {"tolerance_pct": 2.5}

"""Tests de RuleDefinition, ReconciliationRun, ReconciliationFinding."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.domain.payments.enums import (
    EngineType,
    FindingStatus,
    RunStatus,
    Severity,
    TriggeredBy,
)
from app.core.domain.payments.rules import (
    ReconciliationFinding,
    ReconciliationRun,
    RuleDefinition,
)


# ---------- RuleDefinition ----------


def _rule_kwargs(**overrides):
    base = dict(
        code="REGRA_1",
        name="CNPJ match",
        description="CNPJ da base deve bater com o do PDF",
        severity="high",
        engine_type="sql_deterministic",
        python_handler="app.core.services.payments.rules.regra_1_cnpj",
    )
    base.update(overrides)
    return base


def test_rule_definition_happy():
    rd = RuleDefinition(**_rule_kwargs())
    assert rd.severity is Severity.HIGH
    assert rd.engine_type is EngineType.SQL_DETERMINISTIC
    assert rd.version == 1
    assert rd.is_active is True
    assert rd.threshold_params == {}


def test_rule_definition_with_threshold():
    rd = RuleDefinition(**_rule_kwargs(
        engine_type="math_tolerance",
        threshold_params={"tolerance_pct": 0.5},
    ))
    assert rd.threshold_params["tolerance_pct"] == 0.5


def test_rule_definition_rejects_invalid_severity():
    with pytest.raises(ValidationError):
        RuleDefinition(**_rule_kwargs(severity="critical"))


def test_rule_definition_rejects_invalid_engine_type():
    with pytest.raises(ValidationError):
        RuleDefinition(**_rule_kwargs(engine_type="manual"))


def test_rule_definition_rejects_version_zero():
    with pytest.raises(ValidationError):
        RuleDefinition(**_rule_kwargs(version=0))


# ---------- ReconciliationRun ----------


def test_recon_run_happy():
    run = ReconciliationRun(
        triggered_by="manual",
        rules_executed=["REGRA_1", "REGRA_2"],
        status="running",
    )
    assert run.triggered_by is TriggeredBy.MANUAL
    assert run.status is RunStatus.RUNNING
    assert run.findings_created == 0


def test_recon_run_requires_at_least_one_rule():
    """rules_executed vazio não faz sentido — gatilho de bug do orchestrator."""
    with pytest.raises(ValidationError):
        ReconciliationRun(
            triggered_by="manual",
            rules_executed=[],
            status="running",
        )


def test_recon_run_with_scope_filter():
    run = ReconciliationRun(
        triggered_by="scheduled",
        rules_executed=["REGRA_6_5"],
        scope_filter={"empreiteira": "ABILITY", "since": "2025-01-01"},
        status="completed",
    )
    assert run.scope_filter["empreiteira"] == "ABILITY"


def test_recon_run_rejects_invalid_status():
    with pytest.raises(ValidationError):
        ReconciliationRun(
            triggered_by="manual",
            rules_executed=["R1"],
            status="paused",
        )


# ---------- ReconciliationFinding ----------


def _finding_kwargs(**overrides):
    base = dict(
        run_id=uuid4(),
        rule_id=uuid4(),
        rule_code="REGRA_6_5",
        severity="high",
        purchase_order_documento="4500098765",
        expected_value={"valor": 100.00},
        actual_value={"valor": 105.00},
    )
    base.update(overrides)
    return base


def test_finding_happy():
    f = ReconciliationFinding(**_finding_kwargs())
    assert f.status is FindingStatus.OPEN
    assert f.is_monitored_supplier is True
    assert f.evidence_clause_ids == []


def test_finding_with_value_at_risk():
    f = ReconciliationFinding(**_finding_kwargs(
        value_at_risk_brl=Decimal("5000.00"),
        delta_pct=5.0,
    ))
    assert f.value_at_risk_brl == Decimal("5000.00")


def test_finding_rejects_negative_value_at_risk():
    with pytest.raises(ValidationError):
        ReconciliationFinding(**_finding_kwargs(
            value_at_risk_brl=Decimal("-1"),
        ))


def test_finding_status_transitions():
    """Cada FindingStatus deve ser aceito."""
    for status in ["open", "in_analysis", "accepted_fp", "escalated", "blocked"]:
        f = ReconciliationFinding(**_finding_kwargs(status=status))
        assert f.status.value == status

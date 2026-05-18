"""Tests do FindingDraft → ReconciliationFinding conversion."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.core.domain.payments import FindingStatus, Severity
from app.core.services.payments.rules import FindingDraft


def test_minimal_draft_to_finding():
    draft = FindingDraft(
        rule_code="REGRA_1",
        severity=Severity.HIGH,
        purchase_order_documento="4600012345",
        expected_value={"cnpj": "11111111000111"},
        actual_value={"cnpj": "22222222000122"},
    )
    run_id = uuid4()
    rule_id = uuid4()
    f = draft.to_finding(run_id=run_id, rule_id=rule_id)

    assert f.run_id == run_id
    assert f.rule_id == rule_id
    assert f.rule_code == "REGRA_1"
    assert f.severity is Severity.HIGH
    assert f.status is FindingStatus.OPEN
    assert f.is_monitored_supplier is True
    assert f.evidence_clause_ids == []
    assert f.evidence_pages == []


def test_full_draft_to_finding():
    sup_id = uuid4()
    cm_id = uuid4()
    cv_id = uuid4()
    clause_ids = [uuid4(), uuid4()]
    draft = FindingDraft(
        rule_code="REGRA_LPU",
        severity=Severity.HIGH,
        purchase_order_documento="4500098765",
        purchase_order_item="00010",
        wf_payment_id=42,
        wf_payment_data_pedido=date(2025, 6, 1),
        contract_master_id=cm_id,
        contract_version_id=cv_id,
        supplier_id=sup_id,
        is_monitored_supplier=True,
        expected_value={"preco": 100.00},
        actual_value={"preco": 105.50},
        delta_pct=5.5,
        value_at_risk_brl=Decimal("550.00"),
        evidence_clause_ids=clause_ids,
        evidence_pages=[3, 7],
        reason="preco_acima_lpu",
    )
    f = draft.to_finding(run_id=uuid4(), rule_id=uuid4())

    assert f.wf_payment_id == 42
    assert f.wf_payment_data_pedido == date(2025, 6, 1)
    assert f.supplier_id == sup_id
    assert f.contract_master_id == cm_id
    assert f.contract_version_id == cv_id
    assert f.delta_pct == 5.5
    assert f.value_at_risk_brl == Decimal("550.00")
    assert f.evidence_clause_ids == clause_ids
    assert f.evidence_pages == [3, 7]


def test_draft_unmonitored_supplier():
    draft = FindingDraft(
        rule_code="REGRA_1",
        severity=Severity.HIGH,
        purchase_order_documento="X",
        expected_value={},
        actual_value={},
        is_monitored_supplier=False,
    )
    f = draft.to_finding(run_id=uuid4(), rule_id=uuid4())
    assert f.is_monitored_supplier is False


def test_draft_detected_at_set():
    """to_finding deve setar detected_at em UTC now."""
    from datetime import datetime, timedelta

    before = datetime.utcnow()
    draft = FindingDraft(
        rule_code="X",
        severity=Severity.LOW,
        purchase_order_documento="X",
        expected_value={},
        actual_value={},
    )
    f = draft.to_finding(run_id=uuid4(), rule_id=uuid4())
    after = datetime.utcnow() + timedelta(seconds=1)
    assert before <= f.detected_at <= after

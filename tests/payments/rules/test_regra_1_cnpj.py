"""Tests integrados de regra_1_cnpj — CNPJ match base ↔ PDF."""

from __future__ import annotations

from app.adapters.db.repositories.payments import (
    PgContractMasterRepository,
    PgRuleDefinitionRepository,
    PgSupplierBridgeRepository,
)
from app.core.domain.payments import (
    ContractMaster,
    ReconciliationRun,
    RunStatus,
    Severity,
    SupplierBridge,
    TriggeredBy,
)
from app.core.services.payments.rules import ReconciliationContext
from app.core.services.payments.rules.regra_1_cnpj import regra_1_cnpj


def _sb(cnpj: str, contrato: str, ref: str = "WS-001", emp: str = "X") -> SupplierBridge:
    return SupplierBridge(
        categoria="OBRAS",
        empreiteira=emp,
        contrato_num_sap=contrato,
        ref_ws=ref,
        numero_fornecedor_sap="100200",
        cnpj=cnpj,
    )


async def _ctx_regra_1() -> ReconciliationContext:
    rules_repo = PgRuleDefinitionRepository()
    rule = await rules_repo.get_by_code("REGRA_1")
    assert rule is not None
    run = ReconciliationRun(
        triggered_by=TriggeredBy.MANUAL,
        rules_executed=["REGRA_1"],
        status=RunStatus.RUNNING,
    )
    return ReconciliationContext(run=run, rule=rule)


async def test_emits_finding_on_cnpj_mismatch(test_user_id):
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()

    sb_diff = _sb("22222222000122", "4600099999", ref="WS-DIFF", emp="BETA")
    await sb_repo.bulk_upsert([sb_diff])

    # cm_bad: cnpj diferente do sb_diff → vai gerar finding
    cm_bad = ContractMaster(
        supplier_bridge_id=sb_diff.id,
        contrato_num_sap="4600099999",
        ref_ws="WS-DIFF",
        cnpj="00000000000000",  # divergente
        created_by_id=test_user_id,
        is_monitored=True,
    )
    await cm_repo.create(cm_bad)

    ctx = await _ctx_regra_1()
    findings = [f async for f in regra_1_cnpj(ctx)]

    assert len(findings) == 1
    f = findings[0]
    assert f.rule_code == "REGRA_1"
    assert f.severity is Severity.HIGH
    assert f.contract_master_id == cm_bad.id
    assert f.supplier_id == sb_diff.id
    assert f.expected_value == {"cnpj": "22222222000122"}
    assert f.actual_value == {"cnpj": "00000000000000"}
    assert f.purchase_order_documento == "4600099999"


async def test_no_finding_when_cnpjs_match(test_user_id):
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()

    sb = _sb("11111111000111", "4600012345")
    await sb_repo.bulk_upsert([sb])

    cm_ok = ContractMaster(
        supplier_bridge_id=sb.id,
        contrato_num_sap="4600012345",
        ref_ws="WS-001",
        cnpj="11111111000111",  # bate
        created_by_id=test_user_id,
    )
    await cm_repo.create(cm_ok)

    ctx = await _ctx_regra_1()
    findings = [f async for f in regra_1_cnpj(ctx)]
    assert findings == []


async def test_ignores_non_monitored_contracts(test_user_id):
    """is_monitored=FALSE não deve gerar finding mesmo com cnpj divergente."""
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()

    sb = _sb("33333333000133", "4600088888")
    await sb_repo.bulk_upsert([sb])

    cm_unmon = ContractMaster(
        supplier_bridge_id=sb.id,
        contrato_num_sap="4600088888",
        ref_ws="WS-001",
        cnpj="99999999000199",  # divergente
        created_by_id=test_user_id,
        is_monitored=False,
    )
    await cm_repo.create(cm_unmon)

    ctx = await _ctx_regra_1()
    findings = [f async for f in regra_1_cnpj(ctx)]
    assert findings == []


async def test_multiple_mismatches_yield_multiple_findings(test_user_id):
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()

    items = [
        (_sb("11111111000111", "C1"), "00000000000000"),  # diverge
        (_sb("22222222000122", "C2", ref="WS-2"), "22222222000122"),  # OK
        (_sb("33333333000133", "C3", ref="WS-3"), "44444444000144"),  # diverge
    ]
    await sb_repo.bulk_upsert([sb for sb, _ in items])

    for sb, cm_cnpj in items:
        await cm_repo.create(ContractMaster(
            supplier_bridge_id=sb.id,
            contrato_num_sap=sb.contrato_num_sap,
            ref_ws=sb.ref_ws,
            cnpj=cm_cnpj,
            created_by_id=test_user_id,
        ))

    ctx = await _ctx_regra_1()
    findings = [f async for f in regra_1_cnpj(ctx)]
    assert len(findings) == 2
    contratos = {f.purchase_order_documento for f in findings}
    assert contratos == {"C1", "C3"}

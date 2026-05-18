"""Tests integrados de regra_2_validade — Validade + ValFix base ↔ PDF."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.adapters.db.repositories.payments import (
    PgContractMasterRepository,
    PgContractVersionRepository,
    PgPurchaseOrderHeaderRepository,
    PgRuleDefinitionRepository,
    PgSupplierBridgeRepository,
)
from app.core.domain.payments import (
    ContractMaster,
    ContractVersion,
    PurchaseOrderHeader,
    ReconciliationRun,
    RunStatus,
    Severity,
    SupplierBridge,
    TriggeredBy,
)
from app.core.services.payments.rules import ReconciliationContext
from app.core.services.payments.rules.regra_2_validade import regra_2_validade


async def _setup_contract(test_user_id, valid_from: date, valid_to: date,
                          val_fix_cab: Decimal | None = None) -> tuple:
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()

    sb = SupplierBridge(
        categoria="OBRAS", empreiteira="ABILITY",
        contrato_num_sap="C-MAIN", ref_ws="WS-001",
        numero_fornecedor_sap="100200",  # ← match com po.fornecedor
        cnpj="11111111000111",
    )
    await sb_repo.bulk_upsert([sb])

    master = ContractMaster(
        supplier_bridge_id=sb.id,
        contrato_num_sap="C-MAIN", ref_ws="WS-001",
        cnpj="11111111000111", created_by_id=test_user_id,
    )
    await cm_repo.create(master)

    version = ContractVersion(
        contract_master_id=master.id, version_number=1,
        valid_from=valid_from, valid_to=valid_to,
        val_fix_cab=val_fix_cab,
    )
    await cv_repo.create(version)
    return sb, master, version


async def _ctx(threshold_params: dict | None = None) -> ReconciliationContext:
    rules_repo = PgRuleDefinitionRepository()
    rule = await rules_repo.get_by_code("REGRA_2")
    assert rule is not None
    if threshold_params is not None:
        rule.threshold_params = threshold_params  # override local
    run = ReconciliationRun(
        triggered_by=TriggeredBy.MANUAL,
        rules_executed=["REGRA_2"],
        status=RunStatus.RUNNING,
    )
    return ReconciliationContext(run=run, rule=rule)


async def test_finding_when_po_date_outside_version_range(test_user_id, ingestion_run_id):
    """PO com data fora de qualquer ContractVersion vigente → finding."""
    await _setup_contract(test_user_id, date(2024, 1, 1), date(2024, 12, 31))

    po_repo = PgPurchaseOrderHeaderRepository()
    await po_repo.bulk_insert([PurchaseOrderHeader(
        documento_compras="PO-OUT",
        empresa="0001",
        fornecedor="100200",
        data_documento=date(2026, 6, 1),  # fora da vigência
        ingestion_run_id=ingestion_run_id,
    )])

    ctx = await _ctx()
    findings = [f async for f in regra_2_validade(ctx)]

    assert len(findings) == 1
    assert findings[0].rule_code == "REGRA_2"
    assert findings[0].severity is Severity.HIGH
    assert findings[0].purchase_order_documento == "PO-OUT"
    assert findings[0].reason == "sem_versao_vigente"
    assert findings[0].actual_value["contract_version_vigente"] is None


async def test_no_finding_when_po_date_inside_range(test_user_id, ingestion_run_id):
    """PO com data dentro do range da ContractVersion → sem finding."""
    await _setup_contract(test_user_id, date(2024, 1, 1), date(2025, 12, 31))

    po_repo = PgPurchaseOrderHeaderRepository()
    await po_repo.bulk_insert([PurchaseOrderHeader(
        documento_compras="PO-IN",
        empresa="0001",
        fornecedor="100200",
        data_documento=date(2024, 6, 15),
        ingestion_run_id=ingestion_run_id,
    )])

    ctx = await _ctx()
    findings = [f async for f in regra_2_validade(ctx)]
    assert findings == []


async def test_tolerance_days_extends_range(test_user_id, ingestion_run_id):
    """date_tolerance_days expande o range em N dias antes/depois."""
    await _setup_contract(test_user_id, date(2024, 1, 1), date(2024, 1, 31))

    po_repo = PgPurchaseOrderHeaderRepository()
    await po_repo.bulk_insert([PurchaseOrderHeader(
        documento_compras="PO-TOL",
        empresa="0001",
        fornecedor="100200",
        data_documento=date(2024, 2, 5),  # 5 dias fora
        ingestion_run_id=ingestion_run_id,
    )])

    # Sem tolerância → finding
    ctx_strict = await _ctx({"date_tolerance_days": 0})
    findings = [f async for f in regra_2_validade(ctx_strict)]
    assert len(findings) == 1

    # Com 7 dias → cobre, sem finding
    ctx_lenient = await _ctx({"date_tolerance_days": 7})
    findings = [f async for f in regra_2_validade(ctx_lenient)]
    assert findings == []


async def test_finding_when_val_fix_cab_mismatch(test_user_id, ingestion_run_id):
    """ContractVersion vigente mas val_fix_cab divergente → finding."""
    await _setup_contract(
        test_user_id, date(2024, 1, 1), date(2024, 12, 31),
        val_fix_cab=Decimal("1500000.00"),
    )

    po_repo = PgPurchaseOrderHeaderRepository()
    await po_repo.bulk_insert([PurchaseOrderHeader(
        documento_compras="PO-VAL",
        empresa="0001",
        fornecedor="100200",
        data_documento=date(2024, 6, 1),
        val_fix_cab=Decimal("1750000.00"),  # divergente
        ingestion_run_id=ingestion_run_id,
    )])

    ctx = await _ctx()
    findings = [f async for f in regra_2_validade(ctx)]
    assert len(findings) == 1
    f = findings[0]
    assert f.reason == "val_fix_cab_mismatch"
    assert f.expected_value == {"val_fix_cab": 1500000.0}
    assert f.actual_value == {"val_fix_cab": 1750000.0}


async def test_no_finding_when_val_fix_cab_only_one_side(test_user_id, ingestion_run_id):
    """Se PO ou CV não tem val_fix_cab populado → não gera finding de mismatch."""
    await _setup_contract(
        test_user_id, date(2024, 1, 1), date(2024, 12, 31),
        val_fix_cab=Decimal("1500000.00"),
    )

    po_repo = PgPurchaseOrderHeaderRepository()
    await po_repo.bulk_insert([PurchaseOrderHeader(
        documento_compras="PO-NULL",
        empresa="0001",
        fornecedor="100200",
        data_documento=date(2024, 6, 1),
        val_fix_cab=None,  # ausente
        ingestion_run_id=ingestion_run_id,
    )])

    ctx = await _ctx()
    findings = [f async for f in regra_2_validade(ctx)]
    assert findings == []

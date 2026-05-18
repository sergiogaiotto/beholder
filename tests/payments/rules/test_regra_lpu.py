"""Tests integrados de REGRA_LPU — preço ESLL × LPU vigente."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.adapters.db.repositories.payments import (
    PgContractMasterRepository,
    PgContractVersionRepository,
    PgLPUItemRepository,
    PgPurchaseOrderHeaderRepository,
    PgPurchaseOrderItemRepository,
    PgRuleDefinitionRepository,
    PgServicePackageRepository,
    PgSupplierBridgeRepository,
)
from app.core.domain.payments import (
    ContractMaster,
    ContractVersion,
    LPUItem,
    PurchaseOrderHeader,
    PurchaseOrderItem,
    ReconciliationRun,
    RunStatus,
    ServicePackage,
    SourceType,
    SupplierBridge,
    TriggeredBy,
)
from app.core.services.payments.rules import ReconciliationContext
from app.core.services.payments.rules.regra_lpu_preco import regra_lpu_preco


async def _setup_full(
    test_user_id,
    *,
    esll_preco: Decimal,
    lpu_preco: Decimal | None,  # None → no LPU (test servico_fora_da_lpu)
    esll_qtd: Decimal = Decimal("100"),
    contrato: str = "C-LPU",
    pedido: str = "PED-LPU",
    item: str = "00010",
    servico: str = "SVC-1",
) -> tuple:
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()
    po_repo = PgPurchaseOrderHeaderRepository()
    poi_repo = PgPurchaseOrderItemRepository()
    sp_repo = PgServicePackageRepository()
    lpu_repo = PgLPUItemRepository()

    sb = SupplierBridge(
        categoria="OBRAS", empreiteira="ABILITY",
        contrato_num_sap=contrato, ref_ws="WS-LPU",
        numero_fornecedor_sap="FORN-LPU", cnpj="11111111000111",
    )
    await sb_repo.bulk_upsert([sb])

    master = ContractMaster(
        supplier_bridge_id=sb.id, contrato_num_sap=contrato, ref_ws="WS-LPU",
        cnpj="11111111000111", created_by_id=test_user_id,
    )
    await cm_repo.create(master)

    version = ContractVersion(
        contract_master_id=master.id, version_number=1,
        valid_from=date(2024, 1, 1), valid_to=date(2026, 12, 31),
    )
    await cv_repo.create(version)
    await cm_repo.set_current_version(master.id, version.id)

    await po_repo.bulk_insert([PurchaseOrderHeader(
        documento_compras=pedido,
        empresa="0001",
        fornecedor="FORN-LPU",
        data_documento=date(2025, 6, 1),
    )])
    await poi_repo.bulk_insert([PurchaseOrderItem(
        documento_compras=pedido, item=item,
    )])
    await sp_repo.bulk_insert([ServicePackage(
        pacote="PAC-1", linha=1,
        numero_servico=servico,
        preco_bruto=esll_preco,
        qtd_solicitada=esll_qtd,
        ekpo_documento=pedido,
        ekpo_item=item,
    )])

    if lpu_preco is not None:
        await lpu_repo.bulk_insert([LPUItem(
            contract_version_id=version.id,
            documento_compras=contrato,
            numero_servico=servico,
            data_documento=date(2025, 1, 1),
            preco_unitario=lpu_preco,
            source=SourceType.PDF,
            pagina_pdf=7,
        )])
    return version


async def _ctx(params: dict | None = None) -> ReconciliationContext:
    rule = await PgRuleDefinitionRepository().get_by_code("REGRA_LPU")
    assert rule is not None
    if params:
        rule.threshold_params = params
    run = ReconciliationRun(
        triggered_by=TriggeredBy.MANUAL,
        rules_executed=["REGRA_LPU"], status=RunStatus.RUNNING,
    )
    return ReconciliationContext(run=run, rule=rule)


async def test_no_finding_when_preco_match(test_user_id):
    await _setup_full(test_user_id,
                      esll_preco=Decimal("100.00"),
                      lpu_preco=Decimal("100.00"))
    findings = [f async for f in regra_lpu_preco(await _ctx())]
    assert findings == []


async def test_finding_when_preco_fora_tolerance(test_user_id):
    """5% acima da LPU com tolerance 1% → finding."""
    await _setup_full(test_user_id,
                      esll_preco=Decimal("105.00"),
                      lpu_preco=Decimal("100.00"),
                      esll_qtd=Decimal("50"))
    findings = [f async for f in regra_lpu_preco(await _ctx())]
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_code == "REGRA_LPU"
    assert f.reason == "preco_lpu_fora_tolerancia"
    assert f.delta_pct == 5.0
    # var = |105 - 100| * 50 = 250
    assert f.value_at_risk_brl == Decimal("250.00")
    assert f.evidence_pages == [7]
    assert f.expected_value["preco_unitario_lpu"] == 100.0
    assert f.actual_value["preco_bruto_esll"] == 105.0


async def test_finding_when_servico_fora_da_lpu(test_user_id):
    """ESLL existe mas LPU não tem entry pra esse serviço → finding."""
    await _setup_full(test_user_id,
                      esll_preco=Decimal("99.99"),
                      lpu_preco=None)  # nada na LPU
    findings = [f async for f in regra_lpu_preco(await _ctx())]
    assert len(findings) == 1
    f = findings[0]
    assert f.reason == "servico_fora_da_lpu"
    assert f.actual_value["lpu_item_encontrado"] is None
    assert f.evidence_pages == []


async def test_no_finding_within_tolerance(test_user_id):
    """0.5% diff dentro tolerance default 1.0%."""
    await _setup_full(test_user_id,
                      esll_preco=Decimal("100.50"),
                      lpu_preco=Decimal("100.00"))
    findings = [f async for f in regra_lpu_preco(await _ctx())]
    assert findings == []


async def test_custom_tolerance_pct(test_user_id):
    """tolerance 10% aceita 5% diff."""
    await _setup_full(test_user_id,
                      esll_preco=Decimal("105.00"),
                      lpu_preco=Decimal("100.00"))
    findings = [f async for f in regra_lpu_preco(
        await _ctx({"tolerance_pct": 10.0})
    )]
    assert findings == []


async def test_var_brl_zero_qtd_uses_only_delta(test_user_id):
    """ESLL sem qtd_solicitada → var_brl = |delta_preco| sem multiplicar."""
    # Recriamos fixture com qtd=None
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()
    po_repo = PgPurchaseOrderHeaderRepository()
    poi_repo = PgPurchaseOrderItemRepository()
    sp_repo = PgServicePackageRepository()
    lpu_repo = PgLPUItemRepository()

    sb = SupplierBridge(
        categoria="OBRAS", empreiteira="ABILITY",
        contrato_num_sap="C-X", ref_ws="WS-X",
        numero_fornecedor_sap="FORN-X", cnpj="11111111000111",
    )
    await sb_repo.bulk_upsert([sb])
    master = ContractMaster(
        supplier_bridge_id=sb.id, contrato_num_sap="C-X", ref_ws="WS-X",
        cnpj="11111111000111", created_by_id=test_user_id,
    )
    await cm_repo.create(master)
    version = ContractVersion(
        contract_master_id=master.id, version_number=1,
        valid_from=date(2024, 1, 1), valid_to=date(2026, 12, 31),
    )
    await cv_repo.create(version)
    await cm_repo.set_current_version(master.id, version.id)
    await po_repo.bulk_insert([PurchaseOrderHeader(
        documento_compras="PED-X", empresa="0001", fornecedor="FORN-X",
        data_documento=date(2025, 6, 1),
    )])
    await poi_repo.bulk_insert([PurchaseOrderItem(
        documento_compras="PED-X", item="00010",
    )])
    await sp_repo.bulk_insert([ServicePackage(
        pacote="P", linha=1, numero_servico="SVC-X",
        preco_bruto=Decimal("110.00"),
        qtd_solicitada=None,  # sem qtd
        ekpo_documento="PED-X", ekpo_item="00010",
    )])
    await lpu_repo.bulk_insert([LPUItem(
        contract_version_id=version.id,
        documento_compras="C-X", numero_servico="SVC-X",
        data_documento=date(2025, 1, 1),
        preco_unitario=Decimal("100.00"),
        source=SourceType.PDF,
    )])

    findings = [f async for f in regra_lpu_preco(await _ctx())]
    assert len(findings) == 1
    assert findings[0].value_at_risk_brl == Decimal("10.00")  # só delta

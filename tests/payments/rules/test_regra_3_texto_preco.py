"""Tests integrados de regra_3_texto_preco — texto + preço base ↔ PDF."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.adapters.db.repositories.payments import (
    PgContractMasterRepository,
    PgContractVersionRepository,
    PgLPUItemRepository,
    PgPurchaseOrderGcRepository,
    PgRuleDefinitionRepository,
    PgSupplierBridgeRepository,
)
from app.core.domain.payments import (
    ContractMaster,
    ContractVersion,
    LPUItem,
    PurchaseOrderGc,
    ReconciliationRun,
    RunStatus,
    Severity,
    SourceType,
    SupplierBridge,
    TriggeredBy,
)
from app.core.services.payments.rules import ReconciliationContext
from app.core.services.payments.rules.regra_3_texto_preco import (
    regra_3_texto_preco,
)


async def _setup(
    test_user_id,
    *,
    gc_texto: str = "SERV X",
    gc_preco: Decimal = Decimal("100.00"),
    pdf_texto: str = "SERV X",
    pdf_preco: Decimal = Decimal("100.00"),
    contrato: str = "C-3",
) -> None:
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()
    gc_repo = PgPurchaseOrderGcRepository()
    lpu_repo = PgLPUItemRepository()

    sb = SupplierBridge(
        categoria="X", empreiteira="X",
        contrato_num_sap=contrato, ref_ws="WS-3",
        numero_fornecedor_sap="100200", cnpj="11111111000111",
    )
    await sb_repo.bulk_upsert([sb])

    master = ContractMaster(
        supplier_bridge_id=sb.id, contrato_num_sap=contrato, ref_ws="WS-3",
        cnpj="11111111000111", created_by_id=test_user_id,
    )
    await cm_repo.create(master)

    version = ContractVersion(
        contract_master_id=master.id, version_number=1,
        valid_from=date(2024, 1, 1), valid_to=date(2025, 12, 31),
    )
    await cv_repo.create(version)
    await cm_repo.set_current_version(master.id, version.id)

    await gc_repo.bulk_insert([PurchaseOrderGc(
        documento_compras=contrato,
        item="00010",
        empresa="0001",
        numero_servico="SVC-001",
        texto_breve=gc_texto,
        preco_bruto_lpu=gc_preco,
    )])
    await lpu_repo.bulk_insert([LPUItem(
        contract_version_id=version.id,
        documento_compras=contrato,
        numero_servico="SVC-001",
        data_documento=date(2024, 6, 1),
        preco_unitario=pdf_preco,
        descricao=pdf_texto,
        source=SourceType.PDF,
    )])


async def _ctx(threshold_params: dict | None = None) -> ReconciliationContext:
    rules_repo = PgRuleDefinitionRepository()
    rule = await rules_repo.get_by_code("REGRA_3")
    assert rule is not None
    if threshold_params is not None:
        rule.threshold_params = threshold_params
    run = ReconciliationRun(
        triggered_by=TriggeredBy.MANUAL,
        rules_executed=["REGRA_3"], status=RunStatus.RUNNING,
    )
    return ReconciliationContext(run=run, rule=rule)


async def test_no_finding_when_texto_e_preco_match(test_user_id):
    await _setup(test_user_id, gc_texto="X", pdf_texto="X",
                 gc_preco=Decimal("100.00"), pdf_preco=Decimal("100.00"))
    findings = [f async for f in regra_3_texto_preco(await _ctx())]
    assert findings == []


async def test_finding_when_texto_diverge(test_user_id):
    await _setup(test_user_id, gc_texto="MANUTENCAO", pdf_texto="INSTALACAO",
                 gc_preco=Decimal("100.00"), pdf_preco=Decimal("100.00"))
    findings = [f async for f in regra_3_texto_preco(await _ctx())]
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_code == "REGRA_3"
    assert f.severity is Severity.MEDIUM
    assert f.reason == "texto_diverge"
    assert f.expected_value["texto_breve_pdf"] == "INSTALACAO"
    assert f.actual_value["texto_breve_base"] == "MANUTENCAO"


async def test_finding_when_preco_acima_tolerance(test_user_id):
    """Preço 2% acima com tolerance default 1% → finding."""
    await _setup(test_user_id,
                 gc_preco=Decimal("102.00"), pdf_preco=Decimal("100.00"))
    findings = [f async for f in regra_3_texto_preco(await _ctx())]
    assert len(findings) == 1
    f = findings[0]
    assert f.reason == "preco_diverge"
    assert f.delta_pct is not None
    assert abs(f.delta_pct - 2.0) < 0.01


async def test_no_finding_when_preco_dentro_tolerance(test_user_id):
    """Preço 0.5% acima com tolerance default 1% → sem finding."""
    await _setup(test_user_id,
                 gc_preco=Decimal("100.50"), pdf_preco=Decimal("100.00"))
    findings = [f async for f in regra_3_texto_preco(await _ctx())]
    assert findings == []


async def test_finding_when_texto_e_preco_divergem(test_user_id):
    await _setup(test_user_id, gc_texto="A", pdf_texto="B",
                 gc_preco=Decimal("110.00"), pdf_preco=Decimal("100.00"))
    findings = [f async for f in regra_3_texto_preco(await _ctx())]
    assert len(findings) == 1
    assert findings[0].reason == "texto_e_preco_divergem"


async def test_custom_tolerance_pct(test_user_id):
    """tolerance_pct=5% deve aceitar 4% de diferença."""
    await _setup(test_user_id,
                 gc_preco=Decimal("104.00"), pdf_preco=Decimal("100.00"))
    findings = [f async for f in regra_3_texto_preco(
        await _ctx({"tolerance_pct": 5.0})
    )]
    assert findings == []

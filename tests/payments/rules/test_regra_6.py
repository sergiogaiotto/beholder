"""Tests integrados das 9 sub-regras R6 (Batimento WF × EKPO × GC).

Setup base: 1 supplier monitorado + EKKO/EKPO/GC fixture; WFPayment com
universe predicates OK. Cada sub-regra: happy + finding.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.adapters.db.repositories.payments import (
    PgContractMasterRepository,
    PgPurchaseOrderGcRepository,
    PgPurchaseOrderHeaderRepository,
    PgPurchaseOrderItemRepository,
    PgRuleDefinitionRepository,
    PgSupplierBridgeRepository,
    PgWFPaymentRepository,
)
from app.core.domain.payments import (
    ContractMaster,
    PurchaseOrderGc,
    PurchaseOrderHeader,
    PurchaseOrderItem,
    ReconciliationRun,
    RunStatus,
    Sistema,
    SupplierBridge,
    TriggeredBy,
    WFPayment,
)
from app.core.services.payments.rules import ReconciliationContext
from app.core.services.payments.rules._base import universe_filter_for
from app.core.services.payments.rules.regra_6_1_pedido import regra_6_1_pedido
from app.core.services.payments.rules.regra_6_2_data import regra_6_2_data
from app.core.services.payments.rules.regra_6_3_contrato import regra_6_3_contrato
from app.core.services.payments.rules.regra_6_4_item import regra_6_4_item
from app.core.services.payments.rules.regra_6_5_valor import regra_6_5_valor
from app.core.services.payments.rules.regra_6_6_gc_contrato import regra_6_6_gc_contrato
from app.core.services.payments.rules.regra_6_7_gc_item import regra_6_7_gc_item
from app.core.services.payments.rules.regra_6_8_gc_descricao import regra_6_8_gc_descricao
from app.core.services.payments.rules.regra_6_9_gc_preco import regra_6_9_gc_preco


_WF_BASE = dict(
    sistema=Sistema.WF1,
    status_os="EXECUTADO",
    nivel_gerencial="Em Pagamento",
    malogro="OK",
    empreiteira="ABILITY",
    pedido_num="PED-001",
    contrato_num="C-MAIN",
    item_num="00010",
    item_descricao="MANUTENCAO FIBRA",
    valor_total_final=Decimal("10000.00"),
    valor_unitario=Decimal("100.00"),
)


async def _setup_supplier(test_user_id) -> SupplierBridge:
    """Cria empreiteira monitorada — comum a todos os tests."""
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()

    sb = SupplierBridge(
        categoria="OBRAS", empreiteira="ABILITY",
        contrato_num_sap="C-MAIN", ref_ws="WS-R6",
        numero_fornecedor_sap="100200", cnpj="11111111000111",
    )
    await sb_repo.bulk_upsert([sb])

    master = ContractMaster(
        supplier_bridge_id=sb.id, contrato_num_sap="C-MAIN", ref_ws="WS-R6",
        cnpj="11111111000111", created_by_id=test_user_id,
    )
    await cm_repo.create(master)
    return sb


async def _make_wf(ingestion_run_id, **overrides) -> None:
    kw = {**_WF_BASE, **overrides}
    await PgWFPaymentRepository().bulk_insert([WFPayment(
        os_num="OS-R6",
        data_pedido=date(2025, 6, 1),
        ingestion_run_id=ingestion_run_id,
        **kw,
    )])


async def _ctx(rule_code: str, params: dict | None = None) -> ReconciliationContext:
    rule = await PgRuleDefinitionRepository().get_by_code(rule_code)
    assert rule is not None
    if params:
        rule.threshold_params = params
    run = ReconciliationRun(
        triggered_by=TriggeredBy.MANUAL,
        rules_executed=[rule_code], status=RunStatus.RUNNING,
    )
    return ReconciliationContext(
        run=run, rule=rule, universe_filter=universe_filter_for(rule)
    )


# ============================================================
# R6.1 — wf.pedido_num × EKPO/Header
# ============================================================

async def test_6_1_finding_when_no_ekko(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, pedido_num="PED-ORPHAN")
    findings = [f async for f in regra_6_1_pedido(await _ctx("REGRA_6_1"))]
    assert len(findings) == 1
    assert findings[0].reason == "pedido_inexistente_em_ekpo"
    assert findings[0].purchase_order_documento == "PED-ORPHAN"


async def test_6_1_no_finding_when_ekko_exists(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id)
    await PgPurchaseOrderHeaderRepository().bulk_insert([PurchaseOrderHeader(
        documento_compras="PED-001", empresa="0001", fornecedor="100200",
        data_documento=date(2025, 6, 1),
    )])
    findings = [f async for f in regra_6_1_pedido(await _ctx("REGRA_6_1"))]
    assert findings == []


# ============================================================
# R6.2 — wf.data_pedido × EKKO.data_documento
# ============================================================

async def test_6_2_finding_when_data_fora_tolerance(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id)
    await PgPurchaseOrderHeaderRepository().bulk_insert([PurchaseOrderHeader(
        documento_compras="PED-001", empresa="0001", fornecedor="100200",
        data_documento=date(2025, 5, 1),  # 31 dias antes — > tolerance 7
    )])
    findings = [f async for f in regra_6_2_data(await _ctx("REGRA_6_2"))]
    assert len(findings) == 1
    assert findings[0].reason == "data_pedido_fora_tolerancia"


async def test_6_2_no_finding_within_tolerance(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id)
    await PgPurchaseOrderHeaderRepository().bulk_insert([PurchaseOrderHeader(
        documento_compras="PED-001", empresa="0001", fornecedor="100200",
        data_documento=date(2025, 6, 5),  # 4 dias depois — dentro tolerance 7
    )])
    findings = [f async for f in regra_6_2_data(await _ctx("REGRA_6_2"))]
    assert findings == []


# ============================================================
# R6.3 — wf.contrato_num × EKKO.contrato_basico
# ============================================================

async def test_6_3_finding_when_contrato_diverge(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, contrato_num="C-MAIN")
    await PgPurchaseOrderHeaderRepository().bulk_insert([PurchaseOrderHeader(
        documento_compras="PED-001", empresa="0001", fornecedor="100200",
        contrato_basico="C-OTRO",
    )])
    findings = [f async for f in regra_6_3_contrato(await _ctx("REGRA_6_3"))]
    assert len(findings) == 1
    assert findings[0].reason == "contrato_num_diverge_ekko"


async def test_6_3_no_finding_when_match(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, contrato_num="C-MAIN")
    await PgPurchaseOrderHeaderRepository().bulk_insert([PurchaseOrderHeader(
        documento_compras="PED-001", empresa="0001", fornecedor="100200",
        contrato_basico="C-MAIN",
    )])
    findings = [f async for f in regra_6_3_contrato(await _ctx("REGRA_6_3"))]
    assert findings == []


# ============================================================
# R6.4 — wf.item_num × EKPO.item
# ============================================================

async def test_6_4_finding_when_item_inexistente(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id)
    # Sem EKPO Item correspondente
    findings = [f async for f in regra_6_4_item(await _ctx("REGRA_6_4"))]
    assert len(findings) == 1
    assert findings[0].reason == "item_inexistente_em_ekpo"


async def test_6_4_no_finding_when_item_exists(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id)
    await PgPurchaseOrderItemRepository().bulk_insert([PurchaseOrderItem(
        documento_compras="PED-001", item="00010", valor_liquido=Decimal("10000"),
    )])
    findings = [f async for f in regra_6_4_item(await _ctx("REGRA_6_4"))]
    assert findings == []


# ============================================================
# R6.5 — wf.valor_total_final × EKPO.valor_liquido
# ============================================================

async def test_6_5_finding_when_valor_diverge(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, valor_total_final=Decimal("10500.00"))  # 5% diff
    await PgPurchaseOrderItemRepository().bulk_insert([PurchaseOrderItem(
        documento_compras="PED-001", item="00010", valor_liquido=Decimal("10000.00"),
    )])
    findings = [f async for f in regra_6_5_valor(await _ctx("REGRA_6_5"))]
    assert len(findings) == 1
    assert findings[0].reason == "valor_fora_tolerancia_ekpo"
    assert findings[0].delta_pct == 5.0


async def test_6_5_no_finding_within_tolerance(test_user_id, ingestion_run_id):
    """Default tolerance 0.5% — diferença 0.3% passa."""
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, valor_total_final=Decimal("10030.00"))
    await PgPurchaseOrderItemRepository().bulk_insert([PurchaseOrderItem(
        documento_compras="PED-001", item="00010", valor_liquido=Decimal("10000.00"),
    )])
    findings = [f async for f in regra_6_5_valor(await _ctx("REGRA_6_5"))]
    assert findings == []


# ============================================================
# R6.6 — wf.contrato_num × GC.documento_compras
# ============================================================

async def test_6_6_finding_when_contrato_sem_gc(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, contrato_num="C-ORPHAN")
    findings = [f async for f in regra_6_6_gc_contrato(await _ctx("REGRA_6_6"))]
    assert len(findings) == 1
    assert findings[0].reason == "contrato_inexistente_em_gc"


async def test_6_6_no_finding_with_gc(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, contrato_num="C-GC")
    await PgPurchaseOrderGcRepository().bulk_insert([PurchaseOrderGc(
        documento_compras="C-GC", item="00010", empresa="0001",
    )])
    findings = [f async for f in regra_6_6_gc_contrato(await _ctx("REGRA_6_6"))]
    assert findings == []


# ============================================================
# R6.7 — wf.item_num × GC.item
# ============================================================

async def test_6_7_finding_when_item_sem_gc(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, contrato_num="C-GC2", item_num="00099")
    await PgPurchaseOrderGcRepository().bulk_insert([PurchaseOrderGc(
        documento_compras="C-GC2", item="00010", empresa="0001",
    )])
    findings = [f async for f in regra_6_7_gc_item(await _ctx("REGRA_6_7"))]
    assert len(findings) == 1
    assert findings[0].reason == "item_inexistente_em_gc"


# ============================================================
# R6.8 — wf.item_descricao × GC.texto_breve (fuzzy)
# ============================================================

async def test_6_8_finding_when_descricao_fuzzy_baixa(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, contrato_num="C-D", item_num="00010",
                   item_descricao="INSTALACAO ELETRICA")
    await PgPurchaseOrderGcRepository().bulk_insert([PurchaseOrderGc(
        documento_compras="C-D", item="00010", empresa="0001",
        texto_breve="MANUTENCAO FIBRA",
    )])
    findings = [f async for f in regra_6_8_gc_descricao(await _ctx("REGRA_6_8"))]
    assert len(findings) == 1
    assert findings[0].actual_value["fuzzy_score"] < 0.85


async def test_6_8_no_finding_when_descricao_match(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, contrato_num="C-D2", item_num="00010",
                   item_descricao="MANUTENCAO FIBRA")
    await PgPurchaseOrderGcRepository().bulk_insert([PurchaseOrderGc(
        documento_compras="C-D2", item="00010", empresa="0001",
        texto_breve="MANUTENCAO FIBRA OPTICA",
    )])
    findings = [f async for f in regra_6_8_gc_descricao(await _ctx("REGRA_6_8"))]
    assert findings == []


# ============================================================
# R6.9 — wf.valor_unitario × GC.preco_bruto_lpu
# ============================================================

async def test_6_9_finding_when_preco_diverge(test_user_id, ingestion_run_id):
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, contrato_num="C-P", item_num="00010",
                   valor_unitario=Decimal("110.00"))  # 10% acima
    await PgPurchaseOrderGcRepository().bulk_insert([PurchaseOrderGc(
        documento_compras="C-P", item="00010", empresa="0001",
        preco_bruto_lpu=Decimal("100.00"),
    )])
    findings = [f async for f in regra_6_9_gc_preco(await _ctx("REGRA_6_9"))]
    assert len(findings) == 1
    assert findings[0].reason == "preco_unitario_fora_tolerancia_gc"
    assert findings[0].delta_pct == 10.0


async def test_6_9_no_finding_within_tolerance(test_user_id, ingestion_run_id):
    """Default 1% — 0.5% passa."""
    await _setup_supplier(test_user_id)
    await _make_wf(ingestion_run_id, contrato_num="C-P2", item_num="00010",
                   valor_unitario=Decimal("100.50"))
    await PgPurchaseOrderGcRepository().bulk_insert([PurchaseOrderGc(
        documento_compras="C-P2", item="00010", empresa="0001",
        preco_bruto_lpu=Decimal("100.00"),
    )])
    findings = [f async for f in regra_6_9_gc_preco(await _ctx("REGRA_6_9"))]
    assert findings == []


# ============================================================
# Math helper unit
# ============================================================

def test_within_tolerance_pct_within():
    from app.core.services.payments.rules._math import within_tolerance_pct
    from decimal import Decimal as D
    within, delta = within_tolerance_pct(D("100"), D("100.50"), D("1.0"))
    assert within is True
    assert delta == 0.5


def test_within_tolerance_pct_outside():
    from app.core.services.payments.rules._math import within_tolerance_pct
    from decimal import Decimal as D
    within, delta = within_tolerance_pct(D("100"), D("102"), D("1.0"))
    assert within is False
    assert delta == 2.0


def test_within_tolerance_pct_handles_none():
    from app.core.services.payments.rules._math import within_tolerance_pct
    from decimal import Decimal as D
    assert within_tolerance_pct(None, D("100"), 1.0) == (False, None)
    assert within_tolerance_pct(D("100"), None, 1.0) == (False, None)


def test_within_tolerance_pct_handles_zero_expected():
    from app.core.services.payments.rules._math import within_tolerance_pct
    from decimal import Decimal as D
    assert within_tolerance_pct(D("0"), D("100"), 1.0) == (False, None)

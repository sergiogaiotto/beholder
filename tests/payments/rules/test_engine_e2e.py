"""Acceptance integration test do rules engine — sintético, sem dados reais.

Roda os 20 handlers em sequência sobre fixtures mínimas. Garante que:
  - Todos os 20 codes resolvem (registry + seed)
  - Engine completa sem erro
  - Pelo menos 1 finding é emitido (sanidade)
  - mark_completed é chamado

Para acceptance com dados reais, rodar `scripts/acceptance/run_phase2_engine.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

# Side-effect: registra os 20 handlers
import app.core.services.payments.rules._register_all  # noqa: F401
from app.adapters.db.repositories.payments import (
    PgContractMasterRepository,
    PgContractVersionRepository,
    PgPurchaseOrderHeaderRepository,
    PgPurchaseOrderItemRepository,
    PgReconciliationFindingRepository,
    PgReconciliationRunRepository,
    PgRuleDefinitionRepository,
    PgSupplierBridgeRepository,
    PgWFPaymentRepository,
)
from app.core.domain.payments import (
    ContractMaster,
    ContractVersion,
    PurchaseOrderHeader,
    PurchaseOrderItem,
    RunStatus,
    Sistema,
    SupplierBridge,
    WFPayment,
)
from app.core.services.payments.reconciliation_engine import ReconciliationEngine
from app.core.services.payments.rules._register_all import ALL_RULE_CODES


async def _seed_minimal_fixture(test_user_id, ingestion_run_id):
    """Insere fixture mínima com mismatches que disparam várias regras:
       - SupplierBridge ABILITY com CNPJ X
       - ContractMaster com CNPJ ≠ X (dispara R1)
       - ContractVersion com vários NULL (dispara R4)
       - WFPayment com uf inválida (dispara R5.UF) + sem EKPO (dispara R6.1)
    """
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()
    wf_repo = PgWFPaymentRepository()

    sb = SupplierBridge(
        categoria="OBRAS", empreiteira="ABILITY",
        contrato_num_sap="C-E2E", ref_ws="WS-E2E",
        numero_fornecedor_sap="100200", cnpj="11111111000111",
    )
    await sb_repo.bulk_upsert([sb])

    master = ContractMaster(
        supplier_bridge_id=sb.id, contrato_num_sap="C-E2E", ref_ws="WS-E2E",
        cnpj="22222222000122",  # diverge da SB → R1
        created_by_id=test_user_id,
    )
    await cm_repo.create(master)

    version = ContractVersion(
        contract_master_id=master.id, version_number=1,
        valid_from=date(2024, 1, 1), valid_to=date(2026, 12, 31),
        uf=["RJ"],  # WF vai usar MG → 5.UF
        # 5 campos NULL → R4 dispara (max > 2)
    )
    await cv_repo.create(version)
    await cm_repo.set_current_version(master.id, version.id)

    await wf_repo.bulk_insert([WFPayment(
        os_num="OS-E2E",
        data_pedido=date(2025, 6, 1),
        sistema=Sistema.WF1,
        empreiteira="ABILITY",
        pedido_num="PED-ORPHAN",  # sem EKPO → R6.1
        contrato_num="C-NO-GC",   # sem GC → R6.6
        uf="MG",                  # ≠ ["RJ"] → R5.UF
        item_num="00010",         # sem EKPO item → R6.4
        valor_total_final=Decimal("100.00"),
        status_os="EXECUTADO", nivel_gerencial="Em Pagamento", malogro="OK",
        ingestion_run_id=ingestion_run_id,
    )])


async def test_engine_runs_all_20_handlers_without_error(test_user_id, ingestion_run_id):
    """Engine completa sem exceção com todos os 20 handlers ativos."""
    await _seed_minimal_fixture(test_user_id, ingestion_run_id)

    engine = ReconciliationEngine(
        rule_repo=PgRuleDefinitionRepository(),
        run_repo=PgReconciliationRunRepository(),
        finding_repo=PgReconciliationFindingRepository(),
        batch_size=100,
    )
    run = await engine.run(list(ALL_RULE_CODES))

    assert run.status is RunStatus.COMPLETED
    assert run.rules_executed == list(ALL_RULE_CODES)
    # Fixture sintética dispara pelo menos R1 + R4 + R5.UF + R6.1 + R6.4 + R6.6
    assert run.findings_created >= 6


async def test_engine_findings_distribute_across_rules(test_user_id, ingestion_run_id):
    """Cada regra disparada gera findings com rule_code correspondente."""
    from app.adapters.db.postgres_payments import connect_payments

    await _seed_minimal_fixture(test_user_id, ingestion_run_id)

    engine = ReconciliationEngine(
        rule_repo=PgRuleDefinitionRepository(),
        run_repo=PgReconciliationRunRepository(),
        finding_repo=PgReconciliationFindingRepository(),
        batch_size=100,
    )
    run = await engine.run(list(ALL_RULE_CODES))

    async with connect_payments() as c:
        rows = await c.fetch(
            """
            SELECT rule_code, COUNT(*) AS cnt
            FROM payments.reconciliation_finding
            WHERE run_id = $1
            GROUP BY rule_code
            """,
            run.id,
        )
    by_rule = {r["rule_code"]: r["cnt"] for r in rows}

    # Regras que DEVEM disparar (mismatches conhecidos)
    assert by_rule.get("REGRA_1", 0) >= 1, "CNPJ mismatch"
    assert by_rule.get("REGRA_4", 0) >= 1, "Cobertura insuficiente"
    assert by_rule.get("REGRA_5_UF", 0) >= 1, "UF fora do contrato"
    assert by_rule.get("REGRA_6_1", 0) >= 1, "Pedido sem EKPO"
    assert by_rule.get("REGRA_6_4", 0) >= 1, "Item sem EKPO"
    assert by_rule.get("REGRA_6_6", 0) >= 1, "Contrato sem GC"


async def test_engine_completes_in_reasonable_time(test_user_id, ingestion_run_id):
    """20 handlers sobre fixture mínima (1 WF, 1 contract) deve fechar em <10s."""
    import time

    await _seed_minimal_fixture(test_user_id, ingestion_run_id)

    engine = ReconciliationEngine(
        rule_repo=PgRuleDefinitionRepository(),
        run_repo=PgReconciliationRunRepository(),
        finding_repo=PgReconciliationFindingRepository(),
        batch_size=100,
    )
    start = time.perf_counter()
    run = await engine.run(list(ALL_RULE_CODES))
    elapsed = time.perf_counter() - start

    assert run.status is RunStatus.COMPLETED
    assert elapsed < 10, f"E2E synthetic took {elapsed:.1f}s — esperado <10s"

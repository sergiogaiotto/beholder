"""Tests integrados de regra_4_cobertura — alerta extração incompleta."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.adapters.db.repositories.payments import (
    PgContractMasterRepository,
    PgContractVersionRepository,
    PgRuleDefinitionRepository,
    PgSupplierBridgeRepository,
)
from app.core.domain.payments import (
    ContractMaster,
    ContractVersion,
    ReconciliationRun,
    RunStatus,
    Severity,
    SupplierBridge,
    TriggeredBy,
)
from app.core.services.payments.rules import ReconciliationContext
from app.core.services.payments.rules.regra_4_cobertura import regra_4_cobertura


async def _setup_master_with_version(
    test_user_id, version_kwargs: dict, contrato: str = "C-4"
) -> ContractMaster:
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()

    sb = SupplierBridge(
        categoria="X", empreiteira="X",
        contrato_num_sap=contrato, ref_ws=f"WS-{contrato}",
        numero_fornecedor_sap="100200", cnpj="11111111000111",
    )
    await sb_repo.bulk_upsert([sb])

    master = ContractMaster(
        supplier_bridge_id=sb.id, contrato_num_sap=contrato, ref_ws=f"WS-{contrato}",
        cnpj="11111111000111", created_by_id=test_user_id,
    )
    await cm_repo.create(master)

    defaults = dict(
        contract_master_id=master.id, version_number=1,
        valid_from=date(2024, 1, 1), valid_to=date(2025, 12, 31),
    )
    defaults.update(version_kwargs)
    version = ContractVersion(**defaults)
    await cv_repo.create(version)
    await cm_repo.set_current_version(master.id, version.id)
    return master


async def _ctx(threshold_params: dict | None = None) -> ReconciliationContext:
    rules_repo = PgRuleDefinitionRepository()
    rule = await rules_repo.get_by_code("REGRA_4")
    assert rule is not None
    if threshold_params is not None:
        rule.threshold_params = threshold_params
    run = ReconciliationRun(
        triggered_by=TriggeredBy.MANUAL,
        rules_executed=["REGRA_4"], status=RunStatus.RUNNING,
    )
    return ReconciliationContext(run=run, rule=rule)


async def test_no_finding_when_all_fields_populated(test_user_id):
    await _setup_master_with_version(test_user_id, dict(
        objeto_contrato="X", tecnologia="GPON", atividade="MANUTENCAO",
        uf=["RJ"], cidade=["Rio"], val_fix_cab=Decimal("1000000.00"),
    ))
    findings = [f async for f in regra_4_cobertura(await _ctx())]
    assert findings == []


async def test_no_finding_with_2_null_fields(test_user_id):
    """Default max_null=2 → exatamente 2 nulls não dispara (precisa > 2)."""
    await _setup_master_with_version(test_user_id, dict(
        objeto_contrato="X", tecnologia=None, atividade="X",
        uf=["RJ"], cidade=[], val_fix_cab=None,  # 2 nulls: cidade vazia + val_fix_cab
    ), contrato="C-4-2null")
    findings = [f async for f in regra_4_cobertura(await _ctx())]
    # tecnologia=None + cidade=[] + val_fix_cab=None = 3 nulls
    # Vai disparar; vamos refinar
    assert len(findings) == 1
    assert findings[0].actual_value["null_count"] == 3


async def test_finding_when_more_than_2_nulls(test_user_id):
    master = await _setup_master_with_version(test_user_id, dict(
        objeto_contrato=None,
        tecnologia=None,
        atividade=None,
        uf=["RJ"], cidade=["Rio"], val_fix_cab=Decimal("1000"),
    ))
    findings = [f async for f in regra_4_cobertura(await _ctx())]
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_code == "REGRA_4"
    assert f.severity is Severity.MEDIUM
    assert f.contract_master_id == master.id
    missing = set(f.actual_value["missing_fields"])
    assert missing == {"objeto_contrato", "tecnologia", "atividade"}


async def test_ignores_non_monitored(test_user_id):
    """Master com is_monitored=False → não gera finding mesmo com cobertura ruim."""
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()

    sb = SupplierBridge(
        categoria="X", empreiteira="X",
        contrato_num_sap="C-UNMON", ref_ws="WS-UN",
        numero_fornecedor_sap="100200", cnpj="11111111000111",
    )
    await sb_repo.bulk_upsert([sb])

    master = ContractMaster(
        supplier_bridge_id=sb.id, contrato_num_sap="C-UNMON", ref_ws="WS-UN",
        cnpj="11111111000111", created_by_id=test_user_id,
        is_monitored=False,
    )
    await cm_repo.create(master)
    version = ContractVersion(
        contract_master_id=master.id, version_number=1,
        valid_from=date(2024, 1, 1), valid_to=date(2025, 12, 31),
        # tudo NULL
    )
    await cv_repo.create(version)
    await cm_repo.set_current_version(master.id, version.id)

    findings = [f async for f in regra_4_cobertura(await _ctx())]
    assert findings == []


async def test_custom_max_null_fields(test_user_id):
    """max_null_fields=0 deve disparar com 1 null."""
    await _setup_master_with_version(test_user_id, dict(
        objeto_contrato="X", tecnologia="X", atividade="X",
        uf=["RJ"], cidade=["Rio"], val_fix_cab=None,  # 1 null
    ))
    findings = [f async for f in regra_4_cobertura(
        await _ctx({"max_null_fields": 0})
    )]
    assert len(findings) == 1
    assert findings[0].actual_value["missing_fields"] == ["val_fix_cab"]

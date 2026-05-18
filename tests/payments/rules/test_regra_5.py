"""Tests integrados das 6 sub-regras R5 (Escopo — UF/CIDADE/TEC/ATIV/CAT/OBJ).

Setup comum: supplier_bridge + contract_master monitorado + contract_version
vigente em data_pedido + wf_payment com universe predicates OK.

Cada sub-regra: 1 happy (sem finding) + 1 negative (gera finding).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.adapters.db.repositories.payments import (
    PgContractMasterRepository,
    PgContractVersionRepository,
    PgRuleDefinitionRepository,
    PgSupplierBridgeRepository,
    PgWFPaymentRepository,
)
from app.core.domain.payments import (
    ContractMaster,
    ContractVersion,
    ReconciliationRun,
    RunStatus,
    Sistema,
    SupplierBridge,
    TriggeredBy,
    WFPayment,
)
from app.core.services.payments.rules import ReconciliationContext
from app.core.services.payments.rules._base import universe_filter_for
from app.core.services.payments.rules.regra_5_atividade import regra_5_atividade
from app.core.services.payments.rules.regra_5_categoria import regra_5_categoria
from app.core.services.payments.rules.regra_5_cidade import regra_5_cidade
from app.core.services.payments.rules.regra_5_objeto import regra_5_objeto
from app.core.services.payments.rules.regra_5_tecnologia import regra_5_tecnologia
from app.core.services.payments.rules.regra_5_uf import regra_5_uf


# Defaults do CV vigente + WF — cada test sobrescreve campo específico
_CV_DEFAULTS = dict(
    objeto_contrato="MANUTENCAO PREVENTIVA",
    tecnologia="FIBRA OPTICA",
    atividade="MANUTENCAO CORRETIVA",
    uf=["RJ", "SP"],
    cidade=["Rio de Janeiro", "Niterói", "Sao Paulo"],
)
_WF_DEFAULTS = dict(
    sistema=Sistema.WF1,
    status_os="EXECUTADO",  # passa universe
    nivel_gerencial="Em Pagamento",  # passa
    malogro="OK",  # passa (!= ERROR)
    objeto_do_contrato="MANUTENCAO PREVENTIVA",
    tecnologia="FIBRA OPTICA",
    atividade="MANUTENCAO CORRETIVA",
    uf="RJ",
    cidade="Rio de Janeiro",
    categoria="CONSTRUCAO",
)


async def _setup(
    test_user_id, ingestion_run_id,
    *,
    cv_overrides: dict | None = None,
    wf_overrides: dict | None = None,
    supplier_categoria: str = "CONSTRUCAO",
    contrato: str = "C-R5",
) -> None:
    """Insere fixture: 1 supplier monitorado + 1 CV vigente + 1 WF."""
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()
    wf_repo = PgWFPaymentRepository()

    sb = SupplierBridge(
        categoria=supplier_categoria,
        empreiteira="ABILITY",
        contrato_num_sap=contrato,
        ref_ws=f"WS-{contrato}",
        numero_fornecedor_sap="100200",
        cnpj="11111111000111",
    )
    await sb_repo.bulk_upsert([sb])

    master = ContractMaster(
        supplier_bridge_id=sb.id,
        contrato_num_sap=contrato,
        ref_ws=f"WS-{contrato}",
        cnpj="11111111000111",
        created_by_id=test_user_id,
    )
    await cm_repo.create(master)

    cv_kw = {**_CV_DEFAULTS}
    if cv_overrides:
        cv_kw.update(cv_overrides)
    version = ContractVersion(
        contract_master_id=master.id, version_number=1,
        valid_from=date(2024, 1, 1), valid_to=date(2026, 12, 31),
        **cv_kw,
    )
    await cv_repo.create(version)
    await cm_repo.set_current_version(master.id, version.id)

    wf_kw = {**_WF_DEFAULTS}
    if wf_overrides:
        wf_kw.update(wf_overrides)
    await wf_repo.bulk_insert([WFPayment(
        os_num="OS-R5",
        data_pedido=date(2025, 6, 1),
        empreiteira="ABILITY",
        valor_total_final=Decimal("1000.00"),
        ingestion_run_id=ingestion_run_id,
        **wf_kw,
    )])


async def _ctx(rule_code: str) -> ReconciliationContext:
    rule = await PgRuleDefinitionRepository().get_by_code(rule_code)
    assert rule is not None
    run = ReconciliationRun(
        triggered_by=TriggeredBy.MANUAL,
        rules_executed=[rule_code], status=RunStatus.RUNNING,
    )
    return ReconciliationContext(
        run=run, rule=rule, universe_filter=universe_filter_for(rule)
    )


# ---------- 5.a UF ----------

async def test_5a_uf_match_no_finding(test_user_id, ingestion_run_id):
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"uf": "RJ"})  # cv tem ["RJ","SP"]
    findings = [f async for f in regra_5_uf(await _ctx("REGRA_5_UF"))]
    assert findings == []


async def test_5a_uf_off_yields_finding(test_user_id, ingestion_run_id):
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"uf": "MG"})
    findings = [f async for f in regra_5_uf(await _ctx("REGRA_5_UF"))]
    assert len(findings) == 1
    assert findings[0].reason == "uf_fora_contrato"
    assert findings[0].actual_value["uf_pagamento"] == "MG"
    assert set(findings[0].expected_value["uf_contrato"]) == {"RJ", "SP"}


# ---------- 5.b Cidade ----------

async def test_5b_cidade_match_normalized(test_user_id, ingestion_run_id):
    """Normalização: 'sao paulo' (wf) bate com 'Sao Paulo' (cv)."""
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"uf": "SP", "cidade": "sao paulo"})
    findings = [f async for f in regra_5_cidade(await _ctx("REGRA_5_CIDADE"))]
    assert findings == []


async def test_5b_cidade_off_yields_finding(test_user_id, ingestion_run_id):
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"uf": "SP", "cidade": "Campinas"})
    findings = [f async for f in regra_5_cidade(await _ctx("REGRA_5_CIDADE"))]
    assert len(findings) == 1
    assert findings[0].reason == "cidade_fora_contrato"


# ---------- 5.c Tecnologia ----------

async def test_5c_tecnologia_fuzzy_pass(test_user_id, ingestion_run_id):
    """'fibra' bate com 'FIBRA OPTICA' (partial_ratio alto)."""
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"tecnologia": "FIBRA"})
    findings = [f async for f in regra_5_tecnologia(await _ctx("REGRA_5_TECNOLOGIA"))]
    assert findings == []


async def test_5c_tecnologia_fuzzy_fail(test_user_id, ingestion_run_id):
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"tecnologia": "3G"})  # ortogonal a 'FIBRA OPTICA'
    findings = [f async for f in regra_5_tecnologia(await _ctx("REGRA_5_TECNOLOGIA"))]
    assert len(findings) == 1
    assert findings[0].reason == "tecnologia_fuzzy_baixa"
    assert findings[0].actual_value["fuzzy_score"] < 0.90


# ---------- 5.d Atividade ----------

async def test_5d_atividade_fuzzy_pass(test_user_id, ingestion_run_id):
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"atividade": "MANUTENCAO"})  # match parcial OK
    findings = [f async for f in regra_5_atividade(await _ctx("REGRA_5_ATIVIDADE"))]
    assert findings == []


async def test_5d_atividade_fuzzy_fail(test_user_id, ingestion_run_id):
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"atividade": "INSTALACAO PRIMEIRA"})  # diferente
    findings = [f async for f in regra_5_atividade(await _ctx("REGRA_5_ATIVIDADE"))]
    assert len(findings) == 1


# ---------- 5.e Categoria ----------

async def test_5e_categoria_fuzzy_pass(test_user_id, ingestion_run_id):
    """'CONSTRUCAO' bate com sb.categoria='CONSTRUCAO'."""
    await _setup(test_user_id, ingestion_run_id,
                 supplier_categoria="CONSTRUCAO",
                 wf_overrides={"categoria": "CONSTRUCAO"})
    findings = [f async for f in regra_5_categoria(await _ctx("REGRA_5_CATEGORIA"))]
    assert findings == []


async def test_5e_categoria_fuzzy_fail(test_user_id, ingestion_run_id):
    await _setup(test_user_id, ingestion_run_id,
                 supplier_categoria="OBRAS CIVIS",
                 wf_overrides={"categoria": "ATIVACAO"})
    findings = [f async for f in regra_5_categoria(await _ctx("REGRA_5_CATEGORIA"))]
    assert len(findings) == 1
    assert findings[0].reason == "categoria_fuzzy_baixa"


# ---------- 5.f Objeto ----------

async def test_5f_objeto_fuzzy_pass(test_user_id, ingestion_run_id):
    """'MANUTENCAO PREVENTIVA' bate com cv.objeto = 'MANUTENCAO PREVENTIVA' (=1.0)."""
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"objeto_do_contrato": "MANUTENCAO PREVENTIVA"})
    findings = [f async for f in regra_5_objeto(await _ctx("REGRA_5_OBJETO"))]
    assert findings == []


async def test_5f_objeto_fuzzy_pass_partial(test_user_id, ingestion_run_id):
    """Substring matching: 'MANUTENCAO PREVENTIVA SITES' bate com 'MANUTENCAO PREVENTIVA' via partial_ratio."""
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"objeto_do_contrato": "MANUTENCAO PREVENTIVA SITES"})
    findings = [f async for f in regra_5_objeto(await _ctx("REGRA_5_OBJETO"))]
    assert findings == []


async def test_5f_objeto_fuzzy_fail(test_user_id, ingestion_run_id):
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"objeto_do_contrato": "INSTALACAO ELETRICA"})
    findings = [f async for f in regra_5_objeto(await _ctx("REGRA_5_OBJETO"))]
    assert len(findings) == 1
    assert findings[0].actual_value["fuzzy_score"] < 0.85


# ---------- Universe filter integration ----------

async def test_universe_filter_excludes_canceled_os(test_user_id, ingestion_run_id):
    """WF com status_os='CANCELADO' não entra no universe → sem finding mesmo com UF errada."""
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"uf": "MG", "status_os": "CANCELADO"})
    findings = [f async for f in regra_5_uf(await _ctx("REGRA_5_UF"))]
    assert findings == []


async def test_universe_filter_excludes_malogro_error(test_user_id, ingestion_run_id):
    await _setup(test_user_id, ingestion_run_id,
                 wf_overrides={"uf": "MG", "malogro": "ERROR"})
    findings = [f async for f in regra_5_uf(await _ctx("REGRA_5_UF"))]
    assert findings == []

"""Integration tests dos 4 repos de contratos."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.adapters.db.repositories.payments import (
    PgContractClauseRepository,
    PgContractMasterRepository,
    PgContractVersionRepository,
    PgSupplierBridgeRepository,
)
from app.core.domain.payments import (
    ContractClause,
    ContractMaster,
    ContractVersion,
    SupplierBridge,
)


# ---------- SupplierBridge ----------


def _sb(**overrides) -> SupplierBridge:
    base = dict(
        categoria="OBRAS CIVIS",
        empreiteira="ABILITY",
        contrato_num_sap="4600012345",
        ref_ws="WS-001",
        numero_fornecedor_sap="100200",
        cnpj="12345678000199",
    )
    base.update(overrides)
    return SupplierBridge(**base)


async def test_supplier_bulk_upsert_idempotent():
    repo = PgSupplierBridgeRepository()
    items = [_sb(), _sb(contrato_num_sap="4600099999", ref_ws="WS-002")]

    n1 = await repo.bulk_upsert(items)
    assert n1 == 2
    assert await repo.count() == 2

    # Mesma upsert — deve atualizar, não duplicar (ON CONFLICT)
    n2 = await repo.bulk_upsert(items)
    assert n2 == 2
    assert await repo.count() == 2


async def test_supplier_get_by_contrato_e_cnpj():
    repo = PgSupplierBridgeRepository()
    await repo.bulk_upsert([
        _sb(contrato_num_sap="4600012345", cnpj="11111111000111"),
        _sb(contrato_num_sap="4600099999", ref_ws="WS-X", cnpj="11111111000111"),
        _sb(contrato_num_sap="4600088888", ref_ws="WS-Y", cnpj="22222222000122"),
    ])

    by_contrato = await repo.get_by_contrato("4600012345")
    assert by_contrato is not None
    assert by_contrato.cnpj == "11111111000111"

    by_cnpj = await repo.get_by_cnpj("11111111000111")
    assert len(by_cnpj) == 2


# ---------- ContractMaster + ContractVersion (FK circular) ----------


async def _create_supplier(repo: PgSupplierBridgeRepository) -> UUID:
    sb = _sb()
    await repo.bulk_upsert([sb])
    return sb.id


async def test_master_create_then_set_current_version(test_user_id):
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()

    supplier_id = await _create_supplier(sb_repo)
    master = ContractMaster(
        supplier_bridge_id=supplier_id,
        contrato_num_sap="4600012345",
        ref_ws="WS-001",
        cnpj="12345678000199",
        created_by_id=test_user_id,
    )
    await cm_repo.create(master)

    version = ContractVersion(
        contract_master_id=master.id,
        version_number=1,
        valid_from=date(2024, 1, 1),
        valid_to=date(2025, 12, 31),
        val_fix_cab=Decimal("1500000.00"),
    )
    await cv_repo.create(version)
    await cm_repo.set_current_version(master.id, version.id)

    fetched = await cm_repo.get(master.id)
    assert fetched.current_version_id == version.id


async def test_version_get_current_returns_matching_period(test_user_id):
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()

    supplier_id = await _create_supplier(sb_repo)
    master = ContractMaster(
        supplier_bridge_id=supplier_id,
        contrato_num_sap="4600012345",
        ref_ws="WS-001",
        cnpj="12345678000199",
        created_by_id=test_user_id,
    )
    await cm_repo.create(master)

    # 2 versões cobrindo períodos diferentes
    v1 = ContractVersion(
        contract_master_id=master.id, version_number=1,
        valid_from=date(2023, 1, 1), valid_to=date(2024, 6, 30),
    )
    v2 = ContractVersion(
        contract_master_id=master.id, version_number=2,
        valid_from=date(2024, 7, 1), valid_to=date(2025, 12, 31),
    )
    await cv_repo.create(v1)
    await cv_repo.create(v2)

    # Em 2023-05: v1 vigente
    cur = await cv_repo.get_current_for_master(master.id, at=date(2023, 5, 1))
    assert cur is not None
    assert cur.id == v1.id

    # Em 2025-01: v2 vigente
    cur = await cv_repo.get_current_for_master(master.id, at=date(2025, 1, 1))
    assert cur is not None
    assert cur.id == v2.id

    # Em 2030-01: sem versão
    cur = await cv_repo.get_current_for_master(master.id, at=date(2030, 1, 1))
    assert cur is None


async def test_master_list_monitored_filters_correctly(test_user_id):
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()

    sid = await _create_supplier(sb_repo)
    m_mon = ContractMaster(
        supplier_bridge_id=sid, contrato_num_sap="4600012345",
        ref_ws="WS-001", cnpj="12345678000199",
        created_by_id=test_user_id, is_monitored=True,
    )
    m_unmon = ContractMaster(
        supplier_bridge_id=sid, contrato_num_sap="4600099999",
        ref_ws="WS-002", cnpj="12345678000199",
        created_by_id=test_user_id, is_monitored=False,
    )
    await cm_repo.create(m_mon)
    await cm_repo.create(m_unmon)

    monitored = await cm_repo.list_monitored()
    assert len(monitored) == 1
    assert monitored[0].id == m_mon.id


# ---------- ContractClause (pgvector) ----------


async def test_clause_bulk_insert_and_get(test_user_id):
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()
    cc_repo = PgContractClauseRepository()

    sid = await _create_supplier(sb_repo)
    master = ContractMaster(
        supplier_bridge_id=sid, contrato_num_sap="X", ref_ws="Y",
        cnpj="Z", created_by_id=test_user_id,
    )
    await cm_repo.create(master)
    version = ContractVersion(
        contract_master_id=master.id, version_number=1,
        valid_from=date(2024, 1, 1), valid_to=date(2025, 12, 31),
    )
    await cv_repo.create(version)

    c1 = ContractClause(
        contract_version_id=version.id,
        texto="Cláusula 1.1 — objeto",
        clausula_numero="1.1",
        secao="Objeto",
        embedding=[0.01] * 1536,
    )
    c2 = ContractClause(
        contract_version_id=version.id,
        texto="Cláusula 2.1 — prazo",
        clausula_numero="2.1",
        secao="Prazo",
    )
    n = await cc_repo.bulk_insert([c1, c2])
    assert n == 2

    fetched = await cc_repo.get(c1.id)
    assert fetched is not None
    assert fetched.embedding is not None
    assert len(fetched.embedding) == 1536
    assert fetched.embedding[0] == pytest.approx(0.01)

    listed = await cc_repo.list_for_version(version.id)
    assert len(listed) == 2


async def test_clause_search_by_embedding_returns_nearest(test_user_id):
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()
    cc_repo = PgContractClauseRepository()

    sid = await _create_supplier(sb_repo)
    master = ContractMaster(
        supplier_bridge_id=sid, contrato_num_sap="X", ref_ws="Y",
        cnpj="Z", created_by_id=test_user_id,
    )
    await cm_repo.create(master)
    version = ContractVersion(
        contract_master_id=master.id, version_number=1,
        valid_from=date(2024, 1, 1), valid_to=date(2025, 12, 31),
    )
    await cv_repo.create(version)

    # 2 cláusulas com embeddings em direções opostas
    near = ContractClause(
        contract_version_id=version.id,
        texto="near",
        embedding=[1.0] * 1536,
    )
    far = ContractClause(
        contract_version_id=version.id,
        texto="far",
        embedding=[-1.0] * 1536,
    )
    await cc_repo.bulk_insert([near, far])

    # Query próximo de `near` — deve vir primeiro
    results = await cc_repo.search_by_embedding(
        [0.99] * 1536, contract_version_id=version.id, limit=2
    )
    assert len(results) == 2
    assert results[0].id == near.id
    assert results[1].id == far.id

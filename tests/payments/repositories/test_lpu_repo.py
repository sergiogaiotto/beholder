"""Integration tests do PgLPUItemRepository."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.adapters.db.repositories.payments import (
    PgContractMasterRepository,
    PgContractVersionRepository,
    PgLPUItemRepository,
    PgSupplierBridgeRepository,
)
from app.core.domain.payments import (
    ContractMaster,
    ContractVersion,
    LPUItem,
    SourceType,
    SupplierBridge,
)


async def _setup_version(test_user_id) -> tuple:
    sb_repo = PgSupplierBridgeRepository()
    cm_repo = PgContractMasterRepository()
    cv_repo = PgContractVersionRepository()

    sb = SupplierBridge(
        categoria="OBRAS",
        empreiteira="ABILITY",
        contrato_num_sap="4600012345",
        ref_ws="WS-001",
        numero_fornecedor_sap="100200",
        cnpj="12345678000199",
    )
    await sb_repo.bulk_upsert([sb])
    master = ContractMaster(
        supplier_bridge_id=sb.id,
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
    )
    await cv_repo.create(version)
    return master.id, version.id


async def test_bulk_insert_hits_partition(test_user_id, ingestion_run_id):
    """3 LPUItems em anos diferentes → 3 partições."""
    _, version_id = await _setup_version(test_user_id)
    repo = PgLPUItemRepository()

    items = [
        LPUItem(
            contract_version_id=version_id,
            documento_compras="4600012345",
            numero_servico=f"SVC-{i}",
            data_documento=date(year, 6, 1),
            preco_unitario=Decimal("100.00"),
            ingestion_run_id=ingestion_run_id,
        )
        for i, year in enumerate([2024, 2025, 2026])
    ]
    n = await repo.bulk_insert(items)
    assert n == 3
    assert await repo.count_total() == 3
    assert await repo.count_by_year(2024) == 1
    assert await repo.count_by_year(2025) == 1
    assert await repo.count_by_year(2026) == 1


async def test_find_by_servico_e_data_uses_period(test_user_id, ingestion_run_id):
    """Só retorna LPUItems cujo ContractVersion estava vigente em `at`."""
    _, version_id = await _setup_version(test_user_id)
    repo = PgLPUItemRepository()

    item = LPUItem(
        contract_version_id=version_id,
        documento_compras="4600012345",
        numero_servico="SVC-001",
        data_documento=date(2024, 6, 15),
        preco_unitario=Decimal("125.50"),
        ingestion_run_id=ingestion_run_id,
        source=SourceType.MSRV5,
    )
    await repo.bulk_insert([item])

    # No período (2024-01-01 a 2025-12-31)
    matches = await repo.find_by_servico_e_data("SVC-001", date(2024, 6, 1))
    assert len(matches) == 1

    # Fora do período (2030)
    matches = await repo.find_by_servico_e_data("SVC-001", date(2030, 6, 1))
    assert matches == []


async def test_empty_bulk_insert_returns_zero(test_user_id):
    repo = PgLPUItemRepository()
    assert await repo.bulk_insert([]) == 0
    assert await repo.count_total() == 0

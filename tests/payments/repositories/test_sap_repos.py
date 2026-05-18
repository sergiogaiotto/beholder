"""Integration tests dos 5 repos SAP."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.adapters.db.repositories.payments import (
    PgCostCenterAccountRepository,
    PgPurchaseOrderGcRepository,
    PgPurchaseOrderHeaderRepository,
    PgPurchaseOrderItemRepository,
    PgServicePackageRepository,
)
from app.core.domain.payments import (
    CostCenterAccount,
    PurchaseOrderGc,
    PurchaseOrderHeader,
    PurchaseOrderItem,
    ServicePackage,
)


async def test_po_header_bulk_insert_and_get(ingestion_run_id):
    repo = PgPurchaseOrderHeaderRepository()
    items = [
        PurchaseOrderHeader(
            documento_compras=f"4500{i:06}",
            empresa="0001",
            fornecedor="100200",
            data_documento=date(2024, 6, 1),
            val_fix_cab=Decimal("100000.00"),
            ingestion_run_id=ingestion_run_id,
        )
        for i in range(3)
    ]
    n = await repo.bulk_insert(items)
    assert n == 3
    assert await repo.count() == 3

    fetched = await repo.get_by_documento("4500000000")
    assert fetched is not None
    assert fetched.empresa == "0001"

    for_fornecedor = await repo.list_for_fornecedor("100200")
    assert len(for_fornecedor) == 3


async def test_po_header_on_conflict_skips(ingestion_run_id):
    """ON CONFLICT (documento_compras) DO NOTHING — re-insert é idempotente."""
    repo = PgPurchaseOrderHeaderRepository()
    item = PurchaseOrderHeader(
        documento_compras="4500000000",
        empresa="0001",
        fornecedor="100200",
        ingestion_run_id=ingestion_run_id,
    )
    await repo.bulk_insert([item])
    await repo.bulk_insert([item])  # conflict — skip
    assert await repo.count() == 1


async def test_po_item_bulk_insert_e_list_for_documento(ingestion_run_id):
    repo = PgPurchaseOrderItemRepository()
    items = [
        PurchaseOrderItem(
            documento_compras="4500000000",
            item=f"{(i+1)*10:05}",
            valor_liquido=Decimal("1000.00"),
            ingestion_run_id=ingestion_run_id,
        )
        for i in range(3)
    ]
    await repo.bulk_insert(items)

    listed = await repo.list_for_documento("4500000000")
    assert len(listed) == 3
    assert {i.item for i in listed} == {"00010", "00020", "00030"}

    fetched = await repo.get("4500000000", "00020")
    assert fetched is not None
    assert fetched.valor_liquido == Decimal("1000.00")


async def test_service_package_bulk_e_list_for_servico(ingestion_run_id):
    repo = PgServicePackageRepository()
    items = [
        ServicePackage(
            pacote="0000000001",
            linha=i,
            numero_servico="SVC-001" if i < 2 else "SVC-002",
            preco_bruto=Decimal("100.00"),
            ingestion_run_id=ingestion_run_id,
        )
        for i in range(3)
    ]
    await repo.bulk_insert(items)
    assert await repo.count() == 3

    for_svc = await repo.list_for_servico("SVC-001")
    assert len(for_svc) == 2


async def test_po_gc_bulk_e_get(ingestion_run_id):
    repo = PgPurchaseOrderGcRepository()
    item = PurchaseOrderGc(
        documento_compras="4600012345",
        item="00010",
        empresa="0001",
        numero_servico="SVC-001",
        preco_bruto_lpu=Decimal("125.50"),
        ingestion_run_id=ingestion_run_id,
    )
    await repo.bulk_insert([item])

    fetched = await repo.get("4600012345", "00010")
    assert fetched is not None
    assert fetched.preco_bruto_lpu == Decimal("125.50")

    by_svc = await repo.list_for_servico("SVC-001")
    assert len(by_svc) == 1


async def test_cca_bulk_upsert_idempotent_e_list():
    repo = PgCostCenterAccountRepository()
    items = [
        CostCenterAccount(centro_de_custo="CC-1", conta_razao="6010101"),
        CostCenterAccount(centro_de_custo="CC-1", conta_razao="6010102"),
        CostCenterAccount(centro_de_custo="CC-2", conta_razao="6010101"),
    ]
    await repo.bulk_upsert(items)
    assert await repo.count() == 3

    # Re-upsert — não duplica
    await repo.bulk_upsert(items)
    assert await repo.count() == 3

    contas = await repo.get_contas_for_cc("CC-1")
    assert contas == ["6010101", "6010102"]

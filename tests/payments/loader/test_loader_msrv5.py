"""Tests integrados do loader contra TXT MSRV5 fixture + DB real."""

from __future__ import annotations

from app.adapters.db.repositories.payments import (
    PgIngestionRunRepository,
    PgLPUItemRepository,
)
from app.core.domain.payments import IngestionStatus, SourceType
from app.core.services.payments.ingestion import load_source_by_path


async def test_msrv5_txt_load_partitioned(msrv5_txt):
    """TXT MSRV5 → LPUItem via parse_msrv5 + bulk_insert; particionado por ano.

    Fixture tem 4 rows: 2022 / 2023 / 2024 / 2025 → 4 partições diferentes.
    """
    result = await load_source_by_path(msrv5_txt, "msrv5")

    assert result.rows_read == 4
    assert result.rows_inserted == 4

    lpu_repo = PgLPUItemRepository()
    assert await lpu_repo.count_total() == 4
    assert await lpu_repo.count_by_year(2022) == 1
    assert await lpu_repo.count_by_year(2023) == 1
    assert await lpu_repo.count_by_year(2024) == 1
    assert await lpu_repo.count_by_year(2025) == 1


async def test_msrv5_load_sets_source_msrv5_default(msrv5_txt):
    """`defaults.source: msrv5` no YAML aplica em todas as rows."""
    from datetime import date

    await load_source_by_path(msrv5_txt, "msrv5")

    lpu_repo = PgLPUItemRepository()
    matches = await lpu_repo.find_by_servico_e_data("9000507", date(2022, 6, 1))
    # 9000507 não está vinculado a contract_version → JOIN não retorna
    # nada. Vamos verificar via SELECT direto:
    from app.adapters.db.postgres_payments import connect_payments
    async with connect_payments() as c:
        rows = await c.fetch(
            "SELECT source FROM payments.lpu_item ORDER BY data_documento"
        )
    sources = {r["source"] for r in rows}
    assert sources == {SourceType.MSRV5.value}


async def test_msrv5_load_marks_completed(msrv5_txt):
    result = await load_source_by_path(msrv5_txt, "msrv5")

    ir_repo = PgIngestionRunRepository()
    run = await ir_repo.get(result.run.id)
    assert run.status is IngestionStatus.COMPLETED
    assert run.source_type == "msrv5_txt"
    assert run.target_table == "payments.lpu_item"


async def test_msrv5_ingestion_run_id_propagates_to_lpu_items(msrv5_txt):
    """Cada LPUItem carregada deve ter ingestion_run_id == run.id."""
    result = await load_source_by_path(msrv5_txt, "msrv5")

    from app.adapters.db.postgres_payments import connect_payments
    async with connect_payments() as c:
        rows = await c.fetch(
            "SELECT ingestion_run_id FROM payments.lpu_item"
        )
    assert len(rows) == 4
    for r in rows:
        assert r["ingestion_run_id"] == result.run.id

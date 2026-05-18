"""Integration tests do PgIngestionRunRepository."""

from __future__ import annotations

import pytest

from app.adapters.db.repositories.payments import PgIngestionRunRepository
from app.core.domain.payments import IngestionRun, IngestionStatus


@pytest.fixture
def repo() -> PgIngestionRunRepository:
    return PgIngestionRunRepository()


async def test_create_then_get_roundtrip(repo: PgIngestionRunRepository):
    run = IngestionRun(
        source_type="xlsx",
        source_filename="EKPO.xlsx",
        source_sha256="abc123",
        source_size_bytes=1024,
        target_table="payments.purchase_order_item",
        metadata={"sheet": "EKPO"},
    )
    await repo.create(run)
    fetched = await repo.get(run.id)
    assert fetched is not None
    assert fetched.id == run.id
    assert fetched.source_type == "xlsx"
    assert fetched.source_sha256 == "abc123"
    assert fetched.source_size_bytes == 1024
    assert fetched.status is IngestionStatus.PENDING
    assert fetched.metadata == {"sheet": "EKPO"}


async def test_mark_running_then_completed(repo: PgIngestionRunRepository):
    run = IngestionRun(
        source_type="msrv5_txt",
        source_filename="msrv5.txt",
        target_table="payments.lpu_item",
    )
    await repo.create(run)

    await repo.mark_running(run.id)
    r = await repo.get(run.id)
    assert r.status is IngestionStatus.RUNNING
    assert r.finished_at is None

    await repo.mark_completed(
        run.id, rows_read=1000, rows_inserted=995, rows_skipped=3, rows_failed=2
    )
    r = await repo.get(run.id)
    assert r.status is IngestionStatus.COMPLETED
    assert r.finished_at is not None
    assert r.rows_read == 1000
    assert r.rows_inserted == 995
    assert r.rows_skipped == 3
    assert r.rows_failed == 2


async def test_mark_failed_records_error(repo: PgIngestionRunRepository):
    run = IngestionRun(
        source_type="pdf",
        source_filename="contract.pdf",
        target_table="payments.extraction_job",
    )
    await repo.create(run)

    await repo.mark_failed(run.id, error_message="OCR returned empty")
    r = await repo.get(run.id)
    assert r.status is IngestionStatus.FAILED
    assert r.error_message == "OCR returned empty"
    assert r.finished_at is not None


async def test_get_nonexistent_returns_none(repo: PgIngestionRunRepository):
    import uuid
    assert await repo.get(uuid.uuid4()) is None


async def test_list_recent_orders_by_started_at_desc(
    repo: PgIngestionRunRepository,
):
    runs = [
        IngestionRun(
            source_type="xlsx",
            source_filename=f"file_{i}.xlsx",
            target_table="payments.wf_payment",
        )
        for i in range(3)
    ]
    for r in runs:
        await repo.create(r)

    listed = await repo.list_recent(limit=10)
    assert len(listed) == 3
    # ordem decrescente — último criado vem primeiro
    assert listed[0].started_at >= listed[-1].started_at

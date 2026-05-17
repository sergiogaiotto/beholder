"""Tests do IngestionRun."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.domain.payments.ingestion import IngestionRun
from app.core.domain.payments.enums import IngestionStatus


def test_happy_minimal():
    run = IngestionRun(
        source_type="xlsx",
        source_filename="EKPO.xlsx",
        target_table="payments.purchase_order_item",
    )
    assert run.status is IngestionStatus.PENDING
    assert run.rows_read == 0
    assert run.rows_failed == 0
    assert run.id is not None
    assert run.finished_at is None
    assert run.metadata == {}


def test_status_accepts_enum_or_string():
    """Pydantic v2 com str Enum aceita ambos os formatos."""
    r1 = IngestionRun(
        source_type="pdf",
        source_filename="x.pdf",
        target_table="payments.extraction_job",
        status="completed",
    )
    assert r1.status is IngestionStatus.COMPLETED

    r2 = IngestionRun(
        source_type="pdf",
        source_filename="x.pdf",
        target_table="payments.extraction_job",
        status=IngestionStatus.FAILED,
    )
    assert r2.status is IngestionStatus.FAILED


def test_invalid_source_type_rejected():
    with pytest.raises(ValidationError, match="source_type"):
        IngestionRun(
            source_type="csv",
            source_filename="x.csv",
            target_table="payments.wf_payment",
        )


def test_invalid_target_table_rejected():
    """target_table sem prefix 'payments.' — gatilho de schema cruzado por erro."""
    with pytest.raises(ValidationError, match="payments."):
        IngestionRun(
            source_type="xlsx",
            source_filename="x.xlsx",
            target_table="public.users",
        )


def test_negative_rows_rejected():
    with pytest.raises(ValidationError):
        IngestionRun(
            source_type="xlsx",
            source_filename="x.xlsx",
            target_table="payments.wf_payment",
            rows_read=-1,
        )


def test_negative_size_rejected():
    with pytest.raises(ValidationError):
        IngestionRun(
            source_type="xlsx",
            source_filename="x.xlsx",
            target_table="payments.wf_payment",
            source_size_bytes=-1,
        )

"""Tests do ExtractionJob."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.domain.payments.enums import ExtractionStatus
from app.core.domain.payments.extraction import ExtractionJob


def _kwargs(**overrides):
    base = dict(
        pdf_storage_key="contracts/2025/abc123.pdf",
        pdf_filename="contract_abc.pdf",
        pdf_size_bytes=1_234_567,
        status="pending",
        uploaded_by_id=uuid4(),
    )
    base.update(overrides)
    return base


def test_happy_minimal():
    job = ExtractionJob(**_kwargs())
    assert job.status is ExtractionStatus.PENDING
    assert job.cost_brl == Decimal("0")
    assert job.contract_master_id is None


def test_happy_with_extraction_results():
    job = ExtractionJob(**_kwargs(
        contract_master_id=uuid4(),
        pdf_pages=42,
        status="review",
        llm_model_used="sabia-4",
        cost_brl=Decimal("0.37"),  # Pré-C: R$0.37/PDF
        extracted_fields={"val_fix_cab": 1500000.00, "objeto": "Manutenção"},
        confidence_per_field={"val_fix_cab": 0.95, "objeto": 0.78},
    ))
    assert job.cost_brl == Decimal("0.37")
    assert job.llm_model_used == "sabia-4"


def test_invalid_status_rejected():
    with pytest.raises(ValidationError):
        ExtractionJob(**_kwargs(status="processing"))


def test_negative_pdf_size_rejected():
    with pytest.raises(ValidationError):
        ExtractionJob(**_kwargs(pdf_size_bytes=-1))


def test_zero_pdf_pages_rejected():
    """pdf_pages=0 não faz sentido — PDF tem pelo menos 1 página."""
    with pytest.raises(ValidationError):
        ExtractionJob(**_kwargs(pdf_pages=0))


def test_negative_cost_rejected():
    with pytest.raises(ValidationError):
        ExtractionJob(**_kwargs(cost_brl=Decimal("-0.01")))


def test_uploaded_by_id_required():
    """uploaded_by_id é NOT NULL no DB."""
    kwargs = _kwargs()
    del kwargs["uploaded_by_id"]
    with pytest.raises(ValidationError, match="uploaded_by_id"):
        ExtractionJob(**kwargs)


def test_status_transitions():
    """Todos os 5 status devem ser aceitos."""
    for s in ["pending", "extracting", "review", "approved", "failed"]:
        job = ExtractionJob(**_kwargs(status=s))
        assert job.status.value == s

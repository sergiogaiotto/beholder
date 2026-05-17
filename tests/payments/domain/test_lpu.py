"""Tests do LPUItem."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.domain.payments.enums import SourceType
from app.core.domain.payments.lpu import LPUItem


def _kwargs(**overrides):
    base = dict(
        documento_compras="4600012345",
        numero_servico="SVC-001",
        data_documento=date(2024, 5, 1),
        preco_unitario=Decimal("125.50"),
    )
    base.update(overrides)
    return base


def test_happy_minimal():
    lpu = LPUItem(**_kwargs())
    assert lpu.id is None  # BIGSERIAL — antes do INSERT
    assert lpu.source is SourceType.MSRV5
    assert lpu.extracted_by_llm is False
    assert lpu.moeda == "BRL"
    assert lpu.raw_extra == {}


def test_with_contract_version():
    lpu = LPUItem(**_kwargs(
        contract_version_id=uuid4(),
        item=1,
        qtd_solicitada=Decimal("10.000"),
        source=SourceType.PDF,
        extracted_by_llm=True,
        confidence=0.92,
    ))
    assert lpu.source is SourceType.PDF
    assert lpu.confidence == 0.92


def test_rejects_negative_preco():
    with pytest.raises(ValidationError):
        LPUItem(**_kwargs(preco_unitario=Decimal("-0.01")))


def test_rejects_negative_quantidade():
    with pytest.raises(ValidationError):
        LPUItem(**_kwargs(qtd_solicitada=Decimal("-1")))


def test_rejects_invalid_source():
    """Pydantic Enum rejeita string fora do catálogo."""
    with pytest.raises(ValidationError):
        LPUItem(**_kwargs(source="csv"))


def test_data_documento_is_required():
    """data_documento é partition key — sempre obrigatório."""
    with pytest.raises(ValidationError, match="data_documento"):
        LPUItem(
            documento_compras="4600012345",
            numero_servico="SVC-001",
            preco_unitario=Decimal("125.50"),
        )


def test_confidence_out_of_range():
    with pytest.raises(ValidationError):
        LPUItem(**_kwargs(confidence=1.5))

"""Sanity tests da PaymentsBaseModel e type aliases.

PaymentsBaseModel + os type aliases (Money, Quantity, Pct01, EmbeddingVector)
são usados por todas as 18 entidades. Bugs aqui propagam silenciosamente.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from app.core.domain.payments.base import (
    EmbeddingVector,
    Money,
    NonNegInt,
    PaymentsBaseModel,
    Pct01,
    PosInt,
    Quantity,
)


# ---------- ConfigDict behavior ----------


class _Sample(PaymentsBaseModel):
    name: str
    count: NonNegInt = 0


def test_extra_field_is_rejected():
    """extra='forbid' impede typos silenciosos."""
    with pytest.raises(ValidationError, match="extra"):
        _Sample(name="x", count=0, unknown_field=1)


def test_from_attributes_accepts_dataclass():
    """model_validate funciona com objetos com atributos (não só dict)."""

    @dataclass
    class _Row:
        name: str
        count: int

    obj = _Sample.model_validate(_Row(name="abc", count=5))
    assert obj.name == "abc"
    assert obj.count == 5


def test_string_whitespace_is_stripped():
    """str_strip_whitespace=True remove espaços em borda."""
    obj = _Sample(name="  hello  ", count=0)
    assert obj.name == "hello"


# ---------- Money / Quantity ----------


class _ValueHolder(BaseModel):
    """BaseModel cru (não PaymentsBaseModel) para testar os type aliases puros."""

    val: Money | None = None
    qty: Quantity | None = None


def test_money_accepts_zero_and_positive():
    obj = _ValueHolder(val=Decimal("0"))
    assert obj.val == Decimal("0")
    obj = _ValueHolder(val=Decimal("123.45"))
    assert obj.val == Decimal("123.45")


def test_money_rejects_negative():
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _ValueHolder(val=Decimal("-1"))


def test_quantity_rejects_negative():
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _ValueHolder(qty=Decimal("-0.001"))


# ---------- Pct01 ----------


class _Holder01(BaseModel):
    pct: Pct01 | None = None


def test_pct01_accepts_0_and_1():
    assert _Holder01(pct=0.0).pct == 0.0
    assert _Holder01(pct=1.0).pct == 1.0
    assert _Holder01(pct=0.5).pct == 0.5


def test_pct01_rejects_out_of_range():
    with pytest.raises(ValidationError):
        _Holder01(pct=1.01)
    with pytest.raises(ValidationError):
        _Holder01(pct=-0.0001)


# ---------- Integer aliases ----------


class _IntHolder(BaseModel):
    n: NonNegInt | None = None
    p: PosInt | None = None


def test_non_neg_int_accepts_zero():
    assert _IntHolder(n=0).n == 0


def test_non_neg_int_rejects_negative():
    with pytest.raises(ValidationError):
        _IntHolder(n=-1)


def test_pos_int_rejects_zero():
    with pytest.raises(ValidationError):
        _IntHolder(p=0)


def test_pos_int_accepts_one():
    assert _IntHolder(p=1).p == 1


# ---------- EmbeddingVector ----------


class _Vec(BaseModel):
    e: EmbeddingVector | None = None


def test_embedding_accepts_1536_dims():
    obj = _Vec(e=[0.1] * 1536)
    assert obj.e is not None
    assert len(obj.e) == 1536


def test_embedding_rejects_wrong_dim():
    with pytest.raises(ValidationError):
        _Vec(e=[0.1] * 100)
    with pytest.raises(ValidationError):
        _Vec(e=[0.1] * 1537)

"""Tests de _helpers.py — parse_date_br, parse_decimal_br, normalize_header."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.adapters.sap.parsers._helpers import (
    normalize_header,
    parse_date_br,
    parse_decimal_br,
)


# ---------- parse_date_br ----------


def test_date_br_dotted():
    assert parse_date_br("06.09.2022") == date(2022, 9, 6)


def test_date_br_slashed():
    assert parse_date_br("06/09/2022") == date(2022, 9, 6)


def test_date_iso():
    assert parse_date_br("2022-09-06") == date(2022, 9, 6)


def test_date_datetime_returns_date():
    assert parse_date_br(datetime(2022, 9, 6, 14, 30)) == date(2022, 9, 6)


def test_date_date_returns_self():
    d = date(2022, 9, 6)
    assert parse_date_br(d) == d


def test_date_none_returns_none():
    assert parse_date_br(None) is None


def test_date_empty_returns_none():
    assert parse_date_br("") is None
    assert parse_date_br("   ") is None


def test_date_invalid_raises():
    with pytest.raises(ValueError, match="formato desconhecido"):
        parse_date_br("hoje")


def test_date_invalid_day_raises():
    """31.02.2024 — fevereiro não tem dia 31."""
    with pytest.raises(ValueError):
        parse_date_br("31.02.2024")


# ---------- parse_decimal_br ----------


def test_decimal_pt_br_with_thousand_separator():
    assert parse_decimal_br("1.234,56") == Decimal("1234.56")


def test_decimal_pt_br_no_thousand():
    assert parse_decimal_br("125,50") == Decimal("125.50")


def test_decimal_iso():
    assert parse_decimal_br("125.50") == Decimal("125.50")


def test_decimal_integer_string():
    assert parse_decimal_br("1234") == Decimal("1234")


def test_decimal_negative_pt_br():
    assert parse_decimal_br("-100,50") == Decimal("-100.50")


def test_decimal_zero():
    assert parse_decimal_br("0,000") == Decimal("0.000")


def test_decimal_native_decimal_passthrough():
    d = Decimal("99.99")
    assert parse_decimal_br(d) is d


def test_decimal_int_to_decimal():
    assert parse_decimal_br(125) == Decimal("125")


def test_decimal_float_to_decimal():
    assert parse_decimal_br(125.5) == Decimal("125.5")


def test_decimal_none_returns_none():
    assert parse_decimal_br(None) is None


def test_decimal_empty_returns_none():
    assert parse_decimal_br("") is None


def test_decimal_invalid_raises():
    with pytest.raises(ValueError, match="formato desconhecido"):
        parse_decimal_br("abc")


# ---------- normalize_header ----------


def test_normalize_strips_whitespace():
    assert normalize_header("  HEADER  ") == "HEADER"


def test_normalize_removes_nbsp():
    # NBSP ( ) é comum em headers exportados de Excel
    assert normalize_header("HEADER ") == "HEADER"


def test_normalize_removes_bom():
    assert normalize_header("﻿HEADER") == "HEADER"


def test_normalize_none_returns_empty():
    assert normalize_header(None) == ""


def test_normalize_preserves_case_and_underscore():
    assert normalize_header("MATERIAL_SERVICO_NUM") == "MATERIAL_SERVICO_NUM"


def test_normalize_idempotent():
    h = "MATERIAL_SERVICO_NUM"
    assert normalize_header(normalize_header(h)) == h

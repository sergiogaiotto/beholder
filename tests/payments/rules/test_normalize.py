"""Tests do helper de normalização para fuzzy matching."""

from __future__ import annotations

from app.core.services.payments.rules._normalize import normalize_text


def test_basic_lowercase():
    assert normalize_text("HELLO") == "hello"


def test_removes_accents():
    assert normalize_text("São Paulo") == "sao paulo"
    assert normalize_text("João") == "joao"
    assert normalize_text("ÇÉÃÕÔ") == "ceaoo"


def test_collapses_whitespace():
    assert normalize_text("  hello   world  ") == "hello world"
    assert normalize_text("tab\there") == "tab here"
    assert normalize_text("line\nbreak") == "line break"


def test_none_returns_empty():
    assert normalize_text(None) == ""


def test_empty_returns_empty():
    assert normalize_text("") == ""
    assert normalize_text("   ") == ""


def test_preserves_punctuation():
    """Hífen e ponto NÃO são removidos — semântica preservada."""
    assert normalize_text("Rio - SP") == "rio - sp"
    assert normalize_text("CEP: 01.234-567") == "cep: 01.234-567"


def test_idempotent():
    s = "São Paulo - Centro"
    once = normalize_text(s)
    twice = normalize_text(once)
    assert once == twice


def test_non_string_coerced():
    """Aceita non-str via str() coerce — defensivo."""
    assert normalize_text(123) == "123"

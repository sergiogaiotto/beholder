"""Tests do wrapper rapidfuzz."""

from __future__ import annotations

from app.core.services.payments.rules._fuzzy import fuzzy_match, fuzzy_score


def test_score_identical_after_normalize():
    assert fuzzy_score("São Paulo", "sao paulo") == 1.0
    assert fuzzy_score("HELLO", "hello") == 1.0


def test_score_zero_for_none():
    assert fuzzy_score(None, "hello") == 0.0
    assert fuzzy_score("hello", None) == 0.0
    assert fuzzy_score(None, None) == 0.0


def test_score_zero_for_empty():
    assert fuzzy_score("", "hello") == 0.0
    assert fuzzy_score("hello", "") == 0.0


def test_score_partial_match():
    """partial_ratio dá score alto para substring match."""
    score = fuzzy_score(
        "MANUTENÇÃO PREVENTIVA SITES",
        "MANUTENÇÃO PREVENTIVA",
    )
    assert 0.95 <= score <= 1.0


def test_score_low_for_different():
    """Strings sem caracteres em comum têm score zero (partial_ratio)."""
    score = fuzzy_score("ABC", "XYZ")
    assert score == 0.0


def test_score_threshold_realistic():
    """Strings com algumas letras em comum têm score moderado mas abaixo de 0.90.

    Documenta o comportamento partial_ratio: encontra qualquer substring match,
    mesmo de 1-2 chars — daí o threshold 0.90 ser importante (filtra ruído).
    """
    score = fuzzy_score("FIBRA ÓTICA", "INSTALAÇÃO ELÉTRICA")
    assert score < 0.90  # confirma que threshold default rejeita


def test_match_threshold_pass():
    matched, score = fuzzy_match("São Paulo", "sao paulo", threshold=0.90)
    assert matched is True
    assert score == 1.0


def test_match_threshold_fail():
    matched, score = fuzzy_match("ABC", "XYZ", threshold=0.90)
    assert matched is False
    assert score < 0.90


def test_match_threshold_edge():
    """Threshold exato passa."""
    matched, score = fuzzy_match("São Paulo", "sao paulo", threshold=1.0)
    assert matched is True


def test_match_threshold_too_strict():
    """Mesmo match perfeito falha se threshold > 1.0."""
    matched, _ = fuzzy_match("hello", "hello", threshold=1.01)
    assert matched is False

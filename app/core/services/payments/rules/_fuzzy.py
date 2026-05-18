"""Fuzzy matching wrapper sobre rapidfuzz — usado por R5.c/d/e/f e R6.8.

Por que partial_ratio (não ratio nem WRatio):
  - partial_ratio compara o melhor substring match — ideal pra contratos
    onde "MANUTENÇÃO PREVENTIVA SITES" deve casar com "MANUTENÇÃO PREVENTIVA"
    (string source mais curta).
  - WRatio compõe múltiplos métodos com pesos heurísticos — mais lento
    e menos previsível pra audit trail.
  - ratio puro penaliza differences em length severamente.

Threshold default 0.90 (R5.c/d/e) bate com SDD §9 v1.1.1; R5.f usa 0.85
porque OBJETO_DO_CONTRATO tem 598 valores (cardinalidade alta → mais
variação aceitável).
"""

from __future__ import annotations

from rapidfuzz.fuzz import partial_ratio

from app.core.services.payments.rules._normalize import normalize_text


def fuzzy_score(a: str | None, b: str | None) -> float:
    """Score 0.0-1.0 (partial_ratio normalizado). 0 se qualquer um é None/empty."""
    na = normalize_text(a)
    nb = normalize_text(b)
    if not na or not nb:
        return 0.0
    return partial_ratio(na, nb) / 100.0


def fuzzy_match(
    a: str | None, b: str | None, *, threshold: float = 0.90
) -> tuple[bool, float]:
    """(matched, score). matched = score >= threshold."""
    score = fuzzy_score(a, b)
    return score >= threshold, score

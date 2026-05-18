"""Helper math_tolerance — usado por R6.5, R6.9, REGRA_LPU.

within_tolerance_pct compara |actual - expected| / |expected| * 100 com
threshold. Retorna (within, delta_pct).

Quando comparar Decimal:
  - None em qualquer lado → (False, None) (handler decide se vira finding
    com reason='valor_ausente')
  - expected == 0 → (False, None) (não dá pra calcular %)
"""

from __future__ import annotations

from decimal import Decimal


def within_tolerance_pct(
    expected: Decimal | None,
    actual: Decimal | None,
    tolerance_pct: Decimal | float,
) -> tuple[bool, float | None]:
    """Retorna (within_tolerance, delta_pct)."""
    if expected is None or actual is None:
        return False, None
    if expected == 0:
        return False, None
    delta_pct = float(abs(actual - expected) / abs(expected) * 100)
    return delta_pct <= float(tolerance_pct), delta_pct

"""Helpers estatísticos puros para os detectores R7.

Implementação em puro Python (sem numpy/scipy/sklearn) para:
  - Manter a dependência do core enxuta (Pydantic + asyncpg + Jinja já bastam)
  - Custo zero de instalação extra
  - Determinismo bit-a-bit em testes

Se algum detector precisar de algoritmos pesados (k-means real, DBSCAN),
adicionar scikit-learn como dependência opcional no requirements_analytics.txt
e gated por feature flag — não bloquear o core sem necessidade.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence


def mean(values: Sequence[float]) -> float:
    """Média aritmética. NaN safe: lança ValueError se vazio."""
    if not values:
        raise ValueError("mean of empty sequence")
    return sum(values) / len(values)


def stdev(values: Sequence[float], *, sample: bool = True) -> float:
    """Desvio padrão. `sample=True` usa N-1 (default — viés corrigido para
    amostras); False usa N (população). Retorna 0 se len<2."""
    n = len(values)
    if n < 2:
        return 0.0
    mu = mean(values)
    var = sum((v - mu) ** 2 for v in values) / (n - 1 if sample else n)
    return math.sqrt(var)


def zscore(value: float, values: Sequence[float]) -> float:
    """Z-score de `value` contra a distribuição `values`. Retorna 0.0 se
    o desvio padrão é zero (amostra constante) — convenção razoável que
    evita inf/NaN e mantém o detector inocente."""
    sd = stdev(values)
    if sd == 0.0:
        return 0.0
    return (value - mean(values)) / sd


def quantile(values: Sequence[float], q: float) -> float:
    """Quantil `q` ∈ [0, 1] via interpolação linear (mesma fórmula do
    numpy.quantile default `linear`). Empty raises."""
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"quantile q must be in [0, 1], got {q}")
    if not values:
        raise ValueError("quantile of empty sequence")
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def iqr_bounds(
    values: Sequence[float],
    *,
    factor: float = 1.5,
) -> tuple[float, float, float, float]:
    """Calcula (Q1, Q3, lower_fence, upper_fence) usando o método clássico
    de Tukey: bounds = Q1 - factor*IQR, Q3 + factor*IQR.

    `factor=1.5` é o padrão (outliers moderados). `factor=3.0` flag apenas
    outliers extremos. Devolve tudo numa tupla pra UI mostrar o range
    de referência junto com o valor desviante.

    Raises ValueError se `values` vazio.
    """
    if not values:
        raise ValueError("iqr_bounds of empty sequence")
    q1 = quantile(values, 0.25)
    q3 = quantile(values, 0.75)
    iqr = q3 - q1
    lower = q1 - factor * iqr
    upper = q3 + factor * iqr
    return q1, q3, lower, upper


def is_outlier_iqr(value: float, values: Sequence[float], *, factor: float = 1.5) -> bool:
    """True se `value` está fora do intervalo [lower_fence, upper_fence]."""
    if len(values) < 4:  # IQR precisa de Q1/Q3 com sentido — < 4 é ruído
        return False
    _q1, _q3, lower, upper = iqr_bounds(values, factor=factor)
    return value < lower or value > upper


def is_outlier_zscore(
    value: float, values: Sequence[float], *, threshold: float = 2.0
) -> bool:
    """True se |z-score(value, values)| > threshold."""
    return abs(zscore(value, values)) > threshold


def decimal_places(value: float) -> int:
    """Quantidade de casas decimais não-zero. 1.50 → 1; 1.501 → 3.
    Usado pelo detector R7_QTD_QUEBRADA pra flagar quantidades atípicas.
    Comparação é estrutural (string), não numérica.
    """
    s = f"{value!r}"
    if "e" in s.lower():
        # Notação científica — retorna alto para flagar.
        return 99
    if "." not in s:
        return 0
    decimals = s.split(".", 1)[1].rstrip("0")
    return len(decimals)

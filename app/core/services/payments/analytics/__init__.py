"""Analytics R7 engine — registry pattern + 11 detectores estatísticos.

Espelha a arquitetura do `rules/` (R1-R6.9, LPU) para manter a base de código
homogênea. Diferenças semânticas:

  - Output em `analytic_finding` (tabela separada de reconciliation_finding).
  - Granularidade: detectores estatísticos podem ser agregados (clustering
    devolve 1 finding por grupo, não por payment). `wf_payment_id` é
    opcional no draft.
  - Engine roda em modo background (não bloqueia ingestão) e pode ser
    chamado independente do reconciliation_engine.

Cada handler é uma função async que recebe `AnalyticContext` e yields
`AnalyticFindingDraft`s. Registrada via decorator `@register("R7_X")`
no módulo próprio (ex: `r7_lpu_outlier.py`).

Uso pelo engine:
    handler = ANALYTICS_REGISTRY[detector.code]
    async for draft in handler(ctx):
        ...

Os módulos individuais são importados via `_register_all` para popular o
registry sem ordem de import frágil.
"""

from __future__ import annotations

from app.core.services.payments.analytics._base import (
    AnalyticContext,
    AnalyticFindingDraft,
    AnalyticHandler,
    universe_filter_for_detector,
)
from app.core.services.payments.analytics._stats import (
    iqr_bounds,
    zscore,
)

ANALYTICS_REGISTRY: dict[str, AnalyticHandler] = {}


def register(code: str):
    """Decorator que adiciona um handler ao ANALYTICS_REGISTRY.

    Code duplicado é fail-fast (sinal de erro de import/copy-paste).
    """

    def decorator(fn: AnalyticHandler) -> AnalyticHandler:
        if code in ANALYTICS_REGISTRY:
            raise ValueError(
                f"detector code {code!r} já registrado por "
                f"{ANALYTICS_REGISTRY[code].__module__}.{ANALYTICS_REGISTRY[code].__name__}; "
                f"tentando re-registrar {fn.__module__}.{fn.__name__}"
            )
        ANALYTICS_REGISTRY[code] = fn
        return fn

    return decorator


__all__ = [
    "ANALYTICS_REGISTRY",
    "register",
    "AnalyticFindingDraft",
    "AnalyticContext",
    "AnalyticHandler",
    "universe_filter_for_detector",
    "iqr_bounds",
    "zscore",
]

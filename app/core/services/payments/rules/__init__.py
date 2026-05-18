"""Rules engine — registry pattern + 20 handlers (R1, R2, R3, R4, 6×R5, 9×R6, LPU).

Cada handler é uma função async que recebe `ReconciliationContext` e yields
`FindingDraft`s. Registrada via decorator `@register("REGRA_X")` no módulo
próprio (ex: `regra_1_cnpj.py`).

Uso pelo engine:
    handler = RULES_REGISTRY[rule_def.code]
    async for draft in handler(ctx):
        ...

Imports laterais (Bloco B-E populam RULES_REGISTRY ao importar os módulos):
    from app.core.services.payments.rules import RULES_REGISTRY
    import app.core.services.payments.rules.regra_1_cnpj  # registra
"""

from __future__ import annotations

from app.core.services.payments.rules._base import (
    FindingDraft,
    ReconciliationContext,
    RuleHandler,
    universe_filter_for,
)
from app.core.services.payments.rules._fuzzy import fuzzy_match, fuzzy_score
from app.core.services.payments.rules._normalize import normalize_text

RULES_REGISTRY: dict[str, RuleHandler] = {}


def register(code: str):
    """Decorator que adiciona um handler ao RULES_REGISTRY.

    Code duplicado é fail-fast (sinal de erro de import/copy-paste).
    """

    def decorator(fn: RuleHandler) -> RuleHandler:
        if code in RULES_REGISTRY:
            raise ValueError(
                f"rule code {code!r} já registrado por "
                f"{RULES_REGISTRY[code].__module__}.{RULES_REGISTRY[code].__name__}; "
                f"tentando re-registrar {fn.__module__}.{fn.__name__}"
            )
        RULES_REGISTRY[code] = fn
        return fn

    return decorator


__all__ = [
    "RULES_REGISTRY",
    "register",
    "FindingDraft",
    "ReconciliationContext",
    "RuleHandler",
    "universe_filter_for",
    "fuzzy_match",
    "fuzzy_score",
    "normalize_text",
]

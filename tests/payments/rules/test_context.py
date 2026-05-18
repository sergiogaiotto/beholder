"""Tests do ReconciliationContext + universe_filter_for."""

from __future__ import annotations

from uuid import uuid4

from app.core.domain.payments import (
    EngineType,
    ReconciliationRun,
    RuleDefinition,
    RunStatus,
    Severity,
    TriggeredBy,
)
from app.core.services.payments.rules import (
    ReconciliationContext,
    universe_filter_for,
)


def _make_rule(threshold_params: dict | None = None) -> RuleDefinition:
    return RuleDefinition(
        code="REGRA_TEST",
        name="x",
        description="x",
        severity=Severity.MEDIUM,
        engine_type=EngineType.SQL_DETERMINISTIC,
        python_handler="x.y.z",
        threshold_params=threshold_params or {},
    )


def _make_run() -> ReconciliationRun:
    return ReconciliationRun(
        triggered_by=TriggeredBy.MANUAL,
        rules_executed=["REGRA_TEST"],
        status=RunStatus.RUNNING,
    )


def test_context_is_frozen():
    """ReconciliationContext é dataclass(frozen=True)."""
    ctx = ReconciliationContext(run=_make_run(), rule=_make_rule())
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.run = _make_run()  # type: ignore[misc]


def test_universe_filter_default_includes_universe_predicates():
    """Sem override no rule, retorna o predicate global do §9 v1.1.1."""
    rule = _make_rule()
    f = universe_filter_for(rule)
    assert "status_os" in f
    assert "EXECUTADO" in f
    assert "EM EXECU" in f
    assert "nivel_gerencial" in f
    assert "Em Pagamento" in f
    assert "Medido" in f
    assert "malogro" in f
    assert "ERROR" in f


def test_universe_filter_override_via_threshold_params():
    """rule.threshold_params['universe_filter'] sobrescreve o default."""
    override = "status_os = 'CUSTOM'"
    rule = _make_rule({"universe_filter": override})
    f = universe_filter_for(rule)
    assert f == override


def test_universe_filter_empty_override_falls_back():
    """String vazia/whitespace no override → usa default."""
    rule = _make_rule({"universe_filter": "   "})
    f = universe_filter_for(rule)
    assert "status_os" in f  # default usado


def test_universe_filter_non_string_override_ignored():
    """Override não-string é ignorado (defensivo)."""
    rule = _make_rule({"universe_filter": 42})
    f = universe_filter_for(rule)
    assert "status_os" in f


# pytest é importado ao final pq usado só num lugar
import pytest

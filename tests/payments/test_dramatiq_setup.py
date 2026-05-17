"""Smoke test do setup dramatiq — broker importável + actor registrado.

Usa StubBroker (in-memory) para não exigir Redis. Em integração real, o gate
da Fase 0 é validado com k6 + docker-compose.dev.yml.
"""

from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _force_test_mode(monkeypatch):
    """Marca processo como modo teste antes do import — broker fica em None."""
    monkeypatch.setenv("DRAMATIQ_TESTS", "1")
    # Limpa caches de import para forçar re-init com a flag
    for mod in [
        "app.adapters.queue.dramatiq_setup",
        "app.adapters.queue",
        "app.workers.healthcheck",
        "app.workers",
    ]:
        sys.modules.pop(mod, None)


def test_dramatiq_setup_imports_without_redis():
    """Em test mode, módulo importa sem tentar conectar no Redis."""
    from app.adapters.queue import dramatiq_setup

    assert dramatiq_setup.broker is None  # test mode → broker placeholder


def test_healthcheck_actor_declared(monkeypatch):
    """Após instalar um StubBroker, o actor `healthcheck` fica registrado."""
    import dramatiq
    from dramatiq.brokers.stub import StubBroker

    stub = StubBroker()
    dramatiq.set_broker(stub)

    # Importa o módulo do actor — `@dramatiq.actor` se registra no broker corrente.
    from app.workers import healthcheck

    actor = healthcheck.healthcheck
    assert actor.actor_name == "healthcheck"
    # O resultado da chamada direta (sem broker) é o dict echoado
    result = actor.fn(payload={"probe": "fase_0"})
    assert result["echo"] == {"probe": "fase_0"}
    assert "hostname" in result
    assert "pid" in result
    assert "timestamp" in result

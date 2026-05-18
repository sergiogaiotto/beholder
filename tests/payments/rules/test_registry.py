"""Tests do RULES_REGISTRY + decorator @register."""

from __future__ import annotations

import pytest

from app.core.services.payments.rules import RULES_REGISTRY, register


def test_register_adds_to_registry():
    code = "REGRA_TEST_REGISTRY_OK"

    @register(code)
    async def _handler(ctx):
        yield  # generator vazio (asyncgen)

    assert code in RULES_REGISTRY
    assert RULES_REGISTRY[code] is _handler

    # cleanup p/ não contaminar outros tests
    del RULES_REGISTRY[code]


def test_register_duplicate_raises():
    code = "REGRA_TEST_DUP"

    @register(code)
    async def _h1(ctx):
        yield

    with pytest.raises(ValueError, match="já registrado"):

        @register(code)
        async def _h2(ctx):
            yield

    del RULES_REGISTRY[code]


def test_register_preserves_handler():
    """Decorator não muta a função — retorna a mesma referência."""
    code = "REGRA_TEST_IDENTITY"

    async def original(ctx):
        yield

    decorated = register(code)(original)
    assert decorated is original

    del RULES_REGISTRY[code]

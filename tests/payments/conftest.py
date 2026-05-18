"""Conftest específico para tests/payments/.

Garante que o schema `payments` é dropado e recriado entre runs de teste.
Sem isso, tabelas criadas em runs anteriores (e potencialmente corrompidas
por migrations parciais durante debug) persistem e podem mascarar bugs.

O conftest pai (`tests/conftest.py`) isola o schema `public` num schema
ephemeral, mas tabelas em `payments.*` são qualificadas e ficam no schema
fixo `payments` — independente da isolação do public. Aqui resolvemos isso.
"""

from __future__ import annotations

import os

import asyncpg
import pytest


_DEFAULT_TEST_DSN = "postgresql://beholder:beholder@127.0.0.1:5432/beholder_test"


def _test_base_dsn() -> str:
    """DSN sem o options=-csearch_path= — para administrar schemas globalmente."""
    return os.environ.get("TEST_DATABASE_URL_BASE", _DEFAULT_TEST_DSN)


@pytest.fixture(scope="session", autouse=True)
def _reset_payments_schema(event_loop):
    """Antes e depois da sessão: dropa o schema `payments` para state limpo.

    Antes: garante que migrations rodem em schema vazio (não topam com
    tabelas pré-existentes em estados intermediários).

    Depois: deixa o test DB limpo pra próxima rodada.
    """
    async def _drop():
        conn = await asyncpg.connect(_test_base_dsn())
        try:
            await conn.execute("DROP SCHEMA IF EXISTS payments CASCADE")
        finally:
            await conn.close()

    event_loop.run_until_complete(_drop())
    yield
    event_loop.run_until_complete(_drop())

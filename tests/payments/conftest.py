"""Conftest específico para tests/payments/.

Garante que o schema `payments` é dropado e recriado entre runs de teste.
Sem isso, tabelas criadas em runs anteriores (e potencialmente corrompidas
por migrations parciais durante debug) persistem e podem mascarar bugs.

O conftest pai (`tests/conftest.py`) isola o schema `public` num schema
ephemeral, mas tabelas em `payments.*` são qualificadas e ficam no schema
fixo `payments` — independente da isolação do public. Aqui resolvemos isso.

Fase 3 add (Bloco B): fixture function-scope `_reset_payments_data_per_test`
que TRUNCA as tabelas transacionais (não catálogos) entre tests. Os tests
de subdir `tests/payments/repositories/` já tinham fixture análoga; agora
fica disponível também para os tests da raiz do pacote.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from app.adapters.db.postgres_payments import connect_payments


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


@pytest.fixture(autouse=True)
async def _reset_payments_data_per_test():
    """TRUNCATE das tabelas transacionais entre tests da raiz tests/payments/.

    Preserva o seed da migration 007 (20 rule_definition + 11 analytic_detector)
    porque o catálogo é imutável dentro de uma sessão — só limpa o que os tests
    criam (suppliers, contratos, payments, runs, findings).

    No-op enquanto o schema ainda não foi aplicado pelo primeiro test (raise
    UndefinedTable é capturado).
    """
    try:
        async with connect_payments() as c:
            await c.execute(
                """
                TRUNCATE
                    payments.reconciliation_finding,
                    payments.analytic_finding,
                    payments.reconciliation_run,
                    payments.extraction_job,
                    payments.contract_clause,
                    payments.lpu_item,
                    payments.contract_version,
                    payments.contract_master,
                    payments.supplier_bridge,
                    payments.purchase_order_item,
                    payments.purchase_order_header,
                    payments.service_package,
                    payments.purchase_order_gc,
                    payments.cost_center_account,
                    payments.wf_payment,
                    payments.ingestion_run
                RESTART IDENTITY CASCADE
                """
            )
            # Limpa rules/detectors inventados por tests; preserva o seed real.
            await c.execute(
                "DELETE FROM payments.rule_definition WHERE code LIKE 'REGRA_TEST%'"
            )
            await c.execute(
                "DELETE FROM payments.analytic_detector WHERE code LIKE 'R7_TEST%'"
            )
    except (asyncpg.exceptions.UndefinedTableError, asyncpg.exceptions.InvalidSchemaNameError):
        # Schema ainda não criado pelo primeiro test desse arquivo — no-op.
        pass
    yield

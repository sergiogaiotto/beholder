"""Conftest dos tests do rules engine.

Espelha pattern de tests/payments/repositories/conftest.py:
  - init_payments_schema (session autouse)
  - TRUNCATE tabelas payments entre tests (preserva catálogos do seed)
  - test_user_id function-scope
  - rule_handler_cleanup garante que tests do engine não vazam handlers
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from uuid import UUID

import pytest

from app.adapters.db.postgres import connect
from app.adapters.db.postgres_payments import connect_payments, init_payments_schema
from app.core.services.payments.rules import RULES_REGISTRY


@pytest.fixture(scope="session", autouse=True)
def _init_payments_for_rules(event_loop):
    """Garante schema payments aplicado antes de qualquer test."""
    event_loop.run_until_complete(init_payments_schema())


@pytest.fixture(autouse=True)
async def _reset_payments_per_test():
    """TRUNCATE tabelas entre tests + limpa catálogos de teste."""
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
        # Preserva o seed do catálogo (20 rules + 11 detectors); só remove
        # entries criadas por tests (códigos com prefixo TEST).
        await c.execute(
            "DELETE FROM payments.rule_definition WHERE code LIKE 'REGRA_TEST%'"
        )
        await c.execute(
            "DELETE FROM payments.analytic_detector WHERE code LIKE 'R7_TEST%'"
        )
    yield


@pytest.fixture
async def test_user_id() -> AsyncGenerator[UUID, None]:
    """User fresh por test — FK pra ContractMaster.created_by_id etc."""
    user_id = uuid.uuid4()
    username = f"rules_test_{uuid.uuid4().hex[:8]}"
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO users (id, username, hashed_password, salt, is_active)
            VALUES ($1::uuid, $2, 'x', 'x', TRUE)
            """,
            str(user_id), username,
        )
    yield user_id


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot do RULES_REGISTRY antes do test, restaura depois.

    Garante que tests que registram handlers temporários não poluam
    outros tests (especialmente importante porque registry é módulo-global).
    """
    snapshot = dict(RULES_REGISTRY)
    yield
    # Remove tudo que foi adicionado durante o test; preserva o que existia.
    added = set(RULES_REGISTRY) - set(snapshot)
    for k in added:
        del RULES_REGISTRY[k]

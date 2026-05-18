"""Fixtures para tests integrados dos 18 repos payments.

Contexto:
  - Conftest pai (`tests/conftest.py`) isola schema `public` num schema
    ephemeral por sessão e TRUNCA `users` entre tests.
  - Conftest payments (`tests/payments/conftest.py`) dropa schema `payments`
    antes/depois da sessão.

Aqui:
  - Aplicamos `init_payments_schema()` uma vez por sessão (cria as 18 tabelas).
  - Fornecemos `test_user_id` function-scope (insere user fresh, sobrevive
    ao TRUNCATE entre tests).
  - Fornecemos `ingestion_run_id` function-scope (cria run vazio,
    aponta como source pros bulk_insert).

Tudo idempotente — pode ser rodado várias vezes sem erro.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from uuid import UUID

import pytest

from app.adapters.db.postgres import connect
from app.adapters.db.postgres_payments import connect_payments, init_payments_schema
from app.adapters.db.repositories.payments import PgIngestionRunRepository
from app.core.domain.payments import IngestionRun, IngestionStatus


@pytest.fixture(scope="session", autouse=True)
def _init_payments_for_repos(event_loop):
    """Garante que as migrations payments estão aplicadas antes de qualquer test."""
    event_loop.run_until_complete(init_payments_schema())


@pytest.fixture
async def test_user_id() -> AsyncGenerator[UUID, None]:
    """Cria um user fresh em cada test (TRUNCATE entre tests apaga).

    Necessário pra FKs como `contract_master.created_by_id`, `extraction_job.uploaded_by_id`.
    """
    user_id = uuid.uuid4()
    username = f"test_{uuid.uuid4().hex[:8]}"
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
async def _reset_payments_per_test():
    """TRUNCATE de tabelas payments entre tests (idempotência local).

    A ordem importa por causa das FKs:
      - findings → run/rule/detector
      - extraction_job → contract_master
      - lpu_item / contract_version → contract_master → supplier_bridge
      - tudo → ingestion_run

    TRUNCATE CASCADE resolve a ordem automaticamente. Não trunca catálogos
    (rule_definition, analytic_detector) — esses vêm do seed da migration 007
    e são imutáveis pros tests.
    """
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
        # Catálogos: preservar seed (20 rules + 11 detectors), eliminar só os
        # codes inventados pelos tests (REGRA_TEST*, R7_TEST*). Sem isso,
        # test_seed_rules_and_detectors vê contagens infladas após o suite.
        await c.execute(
            "DELETE FROM payments.rule_definition WHERE code LIKE 'REGRA_TEST%'"
        )
        await c.execute(
            "DELETE FROM payments.analytic_detector WHERE code LIKE 'R7_TEST%'"
        )
    yield


@pytest.fixture
async def ingestion_run_id() -> AsyncGenerator[UUID, None]:
    """Cria um IngestionRun em status 'pending' — usado como `source` em bulk inserts."""
    run = IngestionRun(
        source_type="xlsx",
        source_filename="test_fixture.xlsx",
        target_table="payments.wf_payment",
        status=IngestionStatus.PENDING,
    )
    repo = PgIngestionRunRepository()
    await repo.create(run)
    yield run.id

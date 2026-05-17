"""Smoke tests para o migration runner — Fase 0/1.

Verifica:
  1. Migrations 001-007 aplicam idempotentemente (rodar 2x não falha)
  2. As 15 entidades + ingestion_run + 9 lpu_item partitions +
     8 wf_payment partitions estão criadas
  3. Seed (20 rule_definitions + 11 analytic_detectors) presente após apply
"""

from __future__ import annotations

import pytest

from app.adapters.db.postgres_payments import (
    connect_payments,
    init_payments_schema,
)


EXPECTED_ENTITY_TABLES = {
    # ingestion_run vem da migration 001 (Fase 0)
    "ingestion_run",
    # contratos (002)
    "supplier_bridge",
    "contract_master",
    "contract_version",
    "lpu_item",
    "contract_clause",
    # SAP (003)
    "purchase_order_header",
    "purchase_order_item",
    "service_package",
    "purchase_order_gc",
    "cost_center_account",
    # WF (004)
    "wf_payment",
    # rules (005)
    "rule_definition",
    "reconciliation_run",
    "reconciliation_finding",
    "extraction_job",
    # analytics (006)
    "analytic_detector",
    "analytic_finding",
}

# Partições criadas pelas migrations 002 e 004
EXPECTED_PARTITIONS = {
    "lpu_item_2018", "lpu_item_2019", "lpu_item_2020", "lpu_item_2021",
    "lpu_item_2022", "lpu_item_2023", "lpu_item_2024", "lpu_item_2025",
    "lpu_item_2026", "lpu_item_default",
    "wf_payment_2024_q4", "wf_payment_2025_q1", "wf_payment_2025_q2",
    "wf_payment_2025_q3", "wf_payment_2025_q4", "wf_payment_2026_q1",
    "wf_payment_2026_q2", "wf_payment_default",
}


@pytest.mark.asyncio
async def test_migrations_apply_idempotently():
    """init_payments_schema() chamado N vezes não falha — idempotência."""
    for _ in range(3):
        await init_payments_schema()


@pytest.mark.asyncio
async def test_all_entity_tables_exist():
    """As 18 entidades + 18 partições estão criadas após migrations."""
    await init_payments_schema()
    async with connect_payments() as conn:
        rows = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'payments' AND table_type = 'BASE TABLE'
            """
        )
        found = {r["table_name"] for r in rows}

    missing = EXPECTED_ENTITY_TABLES - found
    assert not missing, f"Tabelas faltando: {missing}"

    missing_partitions = EXPECTED_PARTITIONS - found
    assert not missing_partitions, f"Partições faltando: {missing_partitions}"


@pytest.mark.asyncio
async def test_seed_rules_and_detectors():
    """Seed 007 popula 20 RuleDefinitions + 11 AnalyticDetectors."""
    await init_payments_schema()
    async with connect_payments() as conn:
        rules_count = await conn.fetchval("SELECT COUNT(*) FROM payments.rule_definition")
        detectors_count = await conn.fetchval(
            "SELECT COUNT(*) FROM payments.analytic_detector"
        )

    # 20 = R1 + R2 + R3 + R4 + 6×R5 + 9×R6 + REGRA_LPU
    assert rules_count == 20, f"Esperado 20 rule_definitions, achei {rules_count}"
    assert detectors_count == 11, f"Esperado 11 analytic_detectors, achei {detectors_count}"


@pytest.mark.asyncio
async def test_pgvector_extension_installed():
    """pgvector deve estar instalado (criado em migration 001)."""
    await init_payments_schema()
    async with connect_payments() as conn:
        ext = await conn.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        )
    assert ext is not None, "Extension `vector` (pgvector) não está instalada"


@pytest.mark.asyncio
async def test_matview_refresh_concurrently():
    """REFRESH MATERIALIZED VIEW CONCURRENTLY funciona (singleton_key index)."""
    await init_payments_schema()
    async with connect_payments() as conn:
        await conn.execute("SELECT payments.refresh_kpis()")
        row = await conn.fetchrow(
            "SELECT * FROM payments.mv_kpis_empreiteiras_wf"
        )
    assert row is not None
    assert row["singleton_key"] == "kpis"
    assert row["regras_ativas"] == 0  # stub Fase 0; preenchido em Fase 3

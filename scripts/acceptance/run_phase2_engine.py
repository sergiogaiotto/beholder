#!/usr/bin/env python
"""Acceptance Fase 2 — rules engine sobre o universo Fase 1 carregado.

Pré-condição: ter rodado `scripts/acceptance/run_phase1_gate.py` primeiro
(carrega 3.7M rows em payments.*).

Roda os 20 handlers (R1-R6.9 + LPU) numa única ReconciliationRun, mede
tempo total e quebra de findings por rule_code.

SDD G3: 4 regras determinísticas (1, 2, 6, LPU) em <30s pra escopo POC
(261 OS). Aqui rodamos as 20 sobre o universo inteiro — tempo realista
~30s-2min dependendo do hardware + bulk insert overhead em findings.

Uso:
    python scripts/acceptance/run_phase2_engine.py

Exit: 0 se engine completa sem erro; 1 se algum handler falha.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# UTF-8 stdout pra Windows (PowerShell default cp1252 não imprime ✓/✗)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.adapters.db.postgres_payments import (  # noqa: E402
    close_payments_pool,
    connect_payments,
    init_payments_schema,
)
from app.adapters.db.repositories.payments import (  # noqa: E402
    PgReconciliationFindingRepository,
    PgReconciliationRunRepository,
    PgRuleDefinitionRepository,
)
from app.core.services.payments.reconciliation_engine import (  # noqa: E402
    ReconciliationEngine,
)

# Side-effect: registra os 20 handlers no RULES_REGISTRY
import app.core.services.payments.rules._register_all  # noqa: E402, F401
from app.core.services.payments.rules._register_all import ALL_RULE_CODES  # noqa: E402


SLA_SECONDS = 600.0  # 10 min — empírico; SDD G3 (30s) era pra POC 261 OS


async def _summary_by_rule(run_id) -> list[tuple[str, int]]:
    """Agrega findings por rule_code do run específico."""
    async with connect_payments() as c:
        rows = await c.fetch(
            """
            SELECT rule_code, COUNT(*) AS cnt
            FROM payments.reconciliation_finding
            WHERE run_id = $1
            GROUP BY rule_code
            ORDER BY rule_code
            """,
            run_id,
        )
    return [(r["rule_code"], int(r["cnt"])) for r in rows]


async def _universe_size() -> int:
    """Conta pagamentos no universe filter (sanidade — confirma Fase 1)."""
    async with connect_payments() as c:
        n = await c.fetchval(
            """
            SELECT COUNT(*) FROM payments.wf_payment
            WHERE status_os IN ('EXECUTADO', 'EM EXECUÇÃO')
              AND nivel_gerencial IN ('Em Pagamento', 'Medido')
              AND malogro <> 'ERROR'
            """
        )
    return int(n or 0)


async def main() -> int:
    print("=" * 76)
    print("Beholder — Fase 2 Rules Engine Acceptance")
    print(f"  Rules    : {len(ALL_RULE_CODES)}")
    print(f"  SLA      : <{SLA_SECONDS:.0f}s ({SLA_SECONDS/60:.1f} min)")
    print("=" * 76)

    await init_payments_schema()

    universe = await _universe_size()
    print(f"\nUniverse filter (WF): {universe:,} pagamentos elegíveis")
    if universe == 0:
        print("⚠ Nenhum pagamento no universe — rode run_phase1_gate.py primeiro.")
        await close_payments_pool()
        return 2

    print(f"\nExecutando engine com {len(ALL_RULE_CODES)} regras...")
    engine = ReconciliationEngine(
        rule_repo=PgRuleDefinitionRepository(),
        run_repo=PgReconciliationRunRepository(),
        finding_repo=PgReconciliationFindingRepository(),
        batch_size=1000,
    )
    start = time.perf_counter()
    try:
        run = await engine.run(list(ALL_RULE_CODES))
    except Exception as exc:
        elapsed = time.perf_counter() - start
        print(f"\n✗ ENGINE FAILED in {elapsed:.1f}s: {type(exc).__name__}: {exc}")
        await close_payments_pool()
        return 1
    elapsed = time.perf_counter() - start

    print(f"\n{'=' * 76}")
    print(f"Run {run.id} — {run.status.value.upper()}")
    print(f"Total findings: {run.findings_created:,}")
    print(f"Total time:     {elapsed:.1f}s ({elapsed/60:.2f} min)")
    print(f"SLA <{SLA_SECONDS:.0f}s: {'✓ PASS' if elapsed < SLA_SECONDS else '✗ FAIL'}")

    print(f"\nBreakdown por regra:")
    summary = await _summary_by_rule(run.id)
    for code, cnt in summary:
        print(f"  {code:25s} {cnt:>10,}")

    await close_payments_pool()
    return 0 if elapsed < SLA_SECONDS else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

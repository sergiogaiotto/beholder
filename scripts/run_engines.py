"""Roda o ReconciliationEngine (20 regras R1-R6.9+LPU) seguido do
AnalyticsEngine (11 detectores R7) sobre os dados reais já ingeridos.

Output: 2 IDs (reconciliation_run, analytics stats) + totais de findings.
Tempo esperado: 5-15 min serial (handlers fazem fuzzy + queries pesadas
contra wf_payment ~750k rows).
"""

from __future__ import annotations

import asyncio
from time import perf_counter

from app.adapters.db.repositories.payments import (
    PgAnalyticDetectorRepository,
    PgAnalyticFindingRepository,
    PgReconciliationFindingRepository,
    PgReconciliationRunRepository,
    PgRuleDefinitionRepository,
)
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.domain.payments import TriggeredBy
from app.core.services.payments.analytics_engine import AnalyticsEngine
from app.core.services.payments.reconciliation_engine import ReconciliationEngine

# Importa side-effect — popula RULES_REGISTRY (20 handlers).
import app.core.services.payments.rules._register_all  # noqa: F401


ALL_RULE_CODES = [
    "REGRA_1", "REGRA_2", "REGRA_3", "REGRA_4",
    "REGRA_5_ATIVIDADE", "REGRA_5_CATEGORIA", "REGRA_5_CIDADE",
    "REGRA_5_OBJETO", "REGRA_5_TECNOLOGIA", "REGRA_5_UF",
    "REGRA_6_1", "REGRA_6_2", "REGRA_6_3", "REGRA_6_4", "REGRA_6_5",
    "REGRA_6_6", "REGRA_6_7", "REGRA_6_8", "REGRA_6_9",
    "REGRA_LPU",
]


async def main() -> int:
    user = await PgUserRepository().get_by_username("sergio.gaiotto")
    if user is None:
        raise SystemExit("usuário não encontrado: sergio.gaiotto")

    # 1. ReconciliationEngine — 20 regras
    print(f"\n=== ReconciliationEngine: {len(ALL_RULE_CODES)} regras ===")
    engine = ReconciliationEngine(
        rule_repo=PgRuleDefinitionRepository(),
        run_repo=PgReconciliationRunRepository(),
        finding_repo=PgReconciliationFindingRepository(),
    )
    t0 = perf_counter()
    run = await engine.run(
        rule_codes=ALL_RULE_CODES,
        triggered_by=TriggeredBy.MANUAL,
        triggered_by_user_id=user.id,
    )
    elapsed = perf_counter() - t0
    print(
        f"  run_id={run.id}  status={run.status.value}  "
        f"findings={run.findings_created or 0}  elapsed={elapsed:.1f}s"
    )
    if run.error_message:
        print(f"  ERROR: {run.error_message}")

    # 2. AnalyticsEngine — todos os 11 R7
    print("\n=== AnalyticsEngine: todos os detectores R7 ativos ===")
    analytics = AnalyticsEngine(
        detector_repo=PgAnalyticDetectorRepository(),
        finding_repo=PgAnalyticFindingRepository(),
    )
    t0 = perf_counter()
    stats = await analytics.run()
    elapsed = perf_counter() - t0
    print(
        f"  executados={stats.detectors_executed}  "
        f"findings_total={stats.findings_created_total}  "
        f"skipped={list(stats.skipped_codes)}  elapsed={elapsed:.1f}s"
    )
    for d in stats.per_detector:
        marker = "ERR" if d.error else "OK "
        err = f" — {d.error[:80]}" if d.error else ""
        print(f"    [{marker}] {d.detector_code:32s} findings={d.findings_created}{err}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

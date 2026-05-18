"""ReconciliationEngine — orquestra execução de regras + persistência de findings.

Fluxo:
    engine = ReconciliationEngine(rule_repo, run_repo, finding_repo)
    run = await engine.run(
        rule_codes=["REGRA_1", "REGRA_2", "REGRA_LPU"],
        scope_filter={"empreiteira": "ABILITY", "since": date(2025,1,1)},
        triggered_by=TriggeredBy.MANUAL,
        triggered_by_user_id=user_id,
    )

Design:
  - Resolve RuleDefinitions via repo (busca por code; aborta se algum ausente)
  - Cria ReconciliationRun(status='running')
  - Para cada regra, resolve handler em RULES_REGISTRY e itera FindingDrafts
  - Persiste em batch (default 500)
  - mark_completed(findings_created) OU mark_failed(error) — sempre marca,
    mesmo em exception (audit trail)

Execução serial (não asyncio.gather). Razão: cada handler abre conexões
no pool dedicado payments; paralelismo crítico (R6 fuzzy + LPU heavy)
estoura pool size (default max 20) e degrada outros endpoints. Paralelizar
fica pra Fase 3 com pool tuning específico.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.domain.payments import (
    ReconciliationRun,
    RunStatus,
    TriggeredBy,
)
from app.core.ports.payments.repositories import (
    ReconciliationFindingRepository,
    ReconciliationRunRepository,
    RuleDefinitionRepository,
)
from app.core.services.payments.rules import (
    RULES_REGISTRY,
    ReconciliationContext,
)
from app.core.services.payments.rules._base import universe_filter_for


class ReconciliationEngine:
    """Orquestrador do rules engine."""

    def __init__(
        self,
        *,
        rule_repo: RuleDefinitionRepository,
        run_repo: ReconciliationRunRepository,
        finding_repo: ReconciliationFindingRepository,
        batch_size: int = 500,
    ) -> None:
        self._rules = rule_repo
        self._runs = run_repo
        self._findings = finding_repo
        self._batch_size = batch_size

    async def run(
        self,
        rule_codes: list[str],
        *,
        scope_filter: dict[str, Any] | None = None,
        triggered_by: TriggeredBy = TriggeredBy.MANUAL,
        triggered_by_user_id: UUID | None = None,
    ) -> ReconciliationRun:
        """Executa as regras em serial, persiste findings, retorna o run final."""
        if not rule_codes:
            raise ValueError("rule_codes vazio — engine precisa pelo menos 1 regra")

        rules = await self._resolve_rules(rule_codes)

        run = ReconciliationRun(
            triggered_by=triggered_by,
            triggered_by_user_id=triggered_by_user_id,
            rules_executed=rule_codes,
            scope_filter=scope_filter,
            status=RunStatus.RUNNING,
        )
        await self._runs.create(run)

        try:
            total_findings = 0
            for rule_def in rules:
                handler = RULES_REGISTRY.get(rule_def.code)
                if handler is None:
                    raise ValueError(
                        f"no handler registered for {rule_def.code!r} — "
                        f"módulo regra_{rule_def.code.lower()}.py não foi importado?"
                    )

                ctx = ReconciliationContext(
                    run=run,
                    rule=rule_def,
                    scope_filter=scope_filter,
                    universe_filter=universe_filter_for(rule_def),
                )

                batch = []
                async for draft in handler(ctx):
                    finding = draft.to_finding(run_id=run.id, rule_id=rule_def.id)
                    batch.append(finding)
                    if len(batch) >= self._batch_size:
                        total_findings += await self._findings.bulk_insert(batch)
                        batch = []
                if batch:
                    total_findings += await self._findings.bulk_insert(batch)

            await self._runs.mark_completed(
                run.id, findings_created=total_findings
            )
        except Exception as exc:
            await self._runs.mark_failed(run.id, error_message=repr(exc))
            raise

        # Re-fetch para retornar estado pós mark_completed
        fresh = await self._runs.get(run.id)
        return fresh if fresh is not None else run

    async def _resolve_rules(self, codes: list[str]):
        """Busca cada code via repo.get_by_code; aborta se algum não existe."""
        resolved = []
        missing = []
        for code in codes:
            rd = await self._rules.get_by_code(code)
            if rd is None:
                missing.append(code)
            else:
                resolved.append(rd)
        if missing:
            raise ValueError(
                f"rule codes não encontrados no catálogo: {missing}"
            )
        return resolved

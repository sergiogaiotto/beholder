"""AnalyticsEngine — orquestra execução dos detectores R7 + persistência
de findings estatísticos (Fase 2.5 Bloco E).

Espelha o ReconciliationEngine mas opera sobre AnalyticDetector +
AnalyticFinding (tabelas separadas — vide migration 006). Inputs/outputs
não dependem do reconciliation_run; engines são ortogonais.

Fluxo típico:

    engine = AnalyticsEngine(
        detector_repo=PgAnalyticDetectorRepository(),
        finding_repo=PgAnalyticFindingRepository(),
    )
    stats = await engine.run()  # roda todos os ativos
    # ou
    stats = await engine.run(detector_codes=['R7_LPU_OUTLIER', 'R7_VALIDADE_VENCIDA'])

Decisão: execução serial (não asyncio.gather) — mesmo argumento do
reconciliation_engine: handlers abrem conexões no pool dedicado payments
(max=20), gather de 11 detectores pode estourar. Paralelismo eficiente
fica pra otimização futura com tuning específico.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.adapters.db.repositories.payments.analytics_repos import (
    PgAnalyticDetectorRepository,
    PgAnalyticFindingRepository,
)
from app.core.domain.payments import AnalyticDetector
from app.core.services.payments.analytics import (
    ANALYTICS_REGISTRY,
    AnalyticContext,
)

# Import side-effect: popula ANALYTICS_REGISTRY com os 11 handlers R7.
from app.core.services.payments.analytics import _register_all  # noqa: F401

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectorRunStats:
    """Resumo da execução de 1 detector — útil pra UI/logs."""
    detector_code: str
    findings_created: int
    error: str | None = None


@dataclass(frozen=True)
class AnalyticsRunStats:
    """Resumo agregado de uma chamada `engine.run()`."""
    detectors_executed: int
    findings_created_total: int
    per_detector: tuple[DetectorRunStats, ...]
    skipped_codes: tuple[str, ...]
    """Codes sem handler registrado — registrados no DB mas não implementados."""


class AnalyticsEngine:
    """Orquestrador do analytics R7 engine."""

    def __init__(
        self,
        *,
        detector_repo: PgAnalyticDetectorRepository | None = None,
        finding_repo: PgAnalyticFindingRepository | None = None,
        batch_size: int = 500,
    ) -> None:
        self._detectors = detector_repo or PgAnalyticDetectorRepository()
        self._findings = finding_repo or PgAnalyticFindingRepository()
        self._batch_size = batch_size

    async def run(
        self,
        *,
        detector_codes: list[str] | None = None,
    ) -> AnalyticsRunStats:
        """Roda os detectores selecionados (ou todos os ativos se None).

        Cada detector roda em sequência. Handlers desconhecidos do
        ANALYTICS_REGISTRY são listados em `skipped_codes`. Falha de um
        detector NÃO interrompe os demais — exception fica em per_detector.
        """
        active = await self._detectors.list_active()
        if detector_codes is not None:
            wanted = set(detector_codes)
            active = [d for d in active if d.code in wanted]
            missing = wanted - {d.code for d in active}
            if missing:
                logger.warning(
                    "AnalyticsEngine: codes solicitados não estão ativos no DB: %s",
                    sorted(missing),
                )

        per_detector: list[DetectorRunStats] = []
        skipped: list[str] = []
        total_findings = 0

        for detector in active:
            handler = ANALYTICS_REGISTRY.get(detector.code)
            if handler is None:
                logger.warning(
                    "detector %s ativo mas sem handler registrado — skip",
                    detector.code,
                )
                skipped.append(detector.code)
                continue
            stats = await self._run_one(detector, handler)
            per_detector.append(stats)
            total_findings += stats.findings_created

        return AnalyticsRunStats(
            detectors_executed=len(per_detector),
            findings_created_total=total_findings,
            per_detector=tuple(per_detector),
            skipped_codes=tuple(skipped),
        )

    async def _run_one(
        self, detector: AnalyticDetector, handler
    ) -> DetectorRunStats:
        """Executa 1 detector e persiste findings em batches."""
        ctx = AnalyticContext(detector=detector)
        batch = []
        created = 0
        try:
            async for draft in handler(ctx):
                batch.append(draft.to_finding(detector_id=detector.id))
                if len(batch) >= self._batch_size:
                    created += await self._findings.bulk_insert(batch)
                    batch = []
            if batch:
                created += await self._findings.bulk_insert(batch)
            return DetectorRunStats(
                detector_code=detector.code, findings_created=created,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("detector %s falhou: %s", detector.code, exc)
            return DetectorRunStats(
                detector_code=detector.code,
                findings_created=created,
                error=repr(exc),
            )

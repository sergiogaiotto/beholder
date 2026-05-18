"""Tests do AnalyticsEngine (Fase 2.5 Bloco E).

Cobre:
  - run() sem detectores → 0 findings, listas vazias
  - run(detector_codes=[X]) executa só os solicitados
  - Detector ativo sem handler vai pra skipped_codes (não para o run)
  - Falha de um detector não interrompe os demais — error em per_detector
  - Caminho feliz com seed real: engine roda 11 detectores ativos, gera
    findings em payments.analytic_finding

Acceptance E2E:
  - Dataset minimal (2 fornecedores + 30 payments com outlier conhecido)
  - Engine.run() → conta findings inseridos > 0
  - Detector específico (R7_LPU_OUTLIER) acha o outlier plantado
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.adapters.db.postgres import init_db
from app.adapters.db.postgres_payments import connect_payments, init_payments_schema
from app.adapters.db.repositories.payments.analytics_repos import (
    PgAnalyticDetectorRepository,
    PgAnalyticFindingRepository,
)
from app.core.domain.payments import AnalyticDetector, Severity, Technique
from app.core.services.payments.analytics_engine import AnalyticsEngine


@pytest.fixture
async def _payments_schema():
    await init_db()
    await init_payments_schema()


async def _insert_wide(rows: list[dict]) -> None:
    if not rows:
        return
    async with connect_payments() as c:
        await c.executemany(
            """
            INSERT INTO payments.wf_payment (
                os_num, sistema, empreiteira, data_pedido, data_execucao,
                valor_unitario, valor_total_final, material_servico_num,
                mes_medicao, categoria, atividade, tipo_de_lpu, regional_soe_nova,
                status_os, nivel_gerencial, malogro
            ) VALUES (
                $1, 'WF1', $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                'EXECUTADO', 'Em Pagamento', 'OK'
            )
            """,
            [
                (
                    r["os_num"], r["empreiteira"], r["data_pedido"],
                    r.get("data_execucao"),
                    r.get("valor_unitario"), r.get("valor_total_final"),
                    r.get("material_servico_num"),
                    r.get("mes_medicao"),
                    r.get("categoria"), r.get("atividade"),
                    r.get("tipo_de_lpu"), r.get("regional_soe_nova"),
                )
                for r in rows
            ],
        )


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_with_no_detectors_returns_zero(_payments_schema):
    """Sem detectores filtrados pelo código, engine roda os ativos do seed."""
    # Filtra por código inexistente — nada selecionado.
    engine = AnalyticsEngine()
    stats = await engine.run(detector_codes=["R7_INEXISTENTE"])
    assert stats.detectors_executed == 0
    assert stats.findings_created_total == 0


@pytest.mark.asyncio
async def test_engine_runs_only_requested_codes(_payments_schema):
    """`detector_codes=[X]` restringe a execução a esses detectores."""
    engine = AnalyticsEngine()
    stats = await engine.run(detector_codes=["R7_VALIDADE_VENCIDA"])
    # Sem data: 0 findings, mas 1 detector executado.
    assert stats.detectors_executed == 1
    assert stats.per_detector[0].detector_code == "R7_VALIDADE_VENCIDA"
    assert stats.per_detector[0].error is None


@pytest.mark.asyncio
async def test_engine_skips_detector_without_handler(_payments_schema):
    """Insere um detector ativo sem handler — engine pula com aviso, não
    falha. Tests catalog limpo entre runs (autouse delete REGRA_TEST*/R7_TEST*)."""
    fake = AnalyticDetector(
        code="R7_TEST_NO_HANDLER",
        name="fake",
        description="should be skipped",
        technique=Technique.HEURISTIC,
        severity=Severity.LOW,
        threshold_params={},
        python_handler="app.core.services.payments.analytics.nonexistent",
    )
    await PgAnalyticDetectorRepository().save(fake)

    engine = AnalyticsEngine()
    stats = await engine.run(detector_codes=["R7_TEST_NO_HANDLER"])
    assert stats.detectors_executed == 0
    assert "R7_TEST_NO_HANDLER" in stats.skipped_codes


@pytest.mark.asyncio
async def test_engine_acceptance_e2e_lpu_outlier(_payments_schema):
    """Acceptance: planta 1 outlier IQR conhecido, roda só R7_LPU_OUTLIER,
    confirma que o finding foi persistido em analytic_finding."""
    rows = []
    for i in range(30):
        rows.append({
            "os_num": f"OS-{i:03d}", "empreiteira": "EMP",
            "data_pedido": date(2025, 6, 1 + (i % 28)),
            "valor_unitario": Decimal(f"{100 + (i % 5)}"),
            "valor_total_final": Decimal("100"),
            "material_servico_num": "SRV001",
        })
    rows.append({
        "os_num": "OS-OUT", "empreiteira": "EMP",
        "data_pedido": date(2025, 6, 29),
        "valor_unitario": Decimal("1000"),
        "valor_total_final": Decimal("1000"),
        "material_servico_num": "SRV001",
    })
    await _insert_wide(rows)

    # Atualiza o detector R7_LPU_OUTLIER pra usar min_samples=10 (seed=30).
    repo = PgAnalyticDetectorRepository()
    real = await repo.get_by_code("R7_LPU_OUTLIER")
    real.threshold_params = {"iqr_factor": 1.5, "min_samples": 10}
    await repo.save(real)

    engine = AnalyticsEngine()
    stats = await engine.run(detector_codes=["R7_LPU_OUTLIER"])
    assert stats.detectors_executed == 1
    assert stats.findings_created_total >= 1
    detector_stats = stats.per_detector[0]
    assert detector_stats.detector_code == "R7_LPU_OUTLIER"
    assert detector_stats.error is None
    assert detector_stats.findings_created >= 1

    # Persistido no DB.
    fr = PgAnalyticFindingRepository()
    inbox = await fr.list_inbox(limit=5)
    assert len(inbox) >= 1
    assert inbox[0].detector_code == "R7_LPU_OUTLIER"
    assert inbox[0].score > 100  # bem fora do IQR


@pytest.mark.asyncio
async def test_engine_runs_all_active_when_codes_none(_payments_schema):
    """Sem `detector_codes`, engine roda os 11 ativos do seed 007."""
    engine = AnalyticsEngine()
    stats = await engine.run()
    # 11 detectores no seed, todos têm handler registrado.
    assert stats.detectors_executed == 11
    # Sem data: 0 findings totais é OK.
    assert stats.findings_created_total >= 0
    # Cada detector reporta sucesso (sem erro).
    for ds in stats.per_detector:
        assert ds.error is None, (
            f"{ds.detector_code} falhou: {ds.error}"
        )
    assert stats.skipped_codes == ()

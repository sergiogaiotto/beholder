"""Tests dos 4 detectores estatísticos do Bloco B (Fase 2.5).

  - R7_LPU_OUTLIER (iqr)
  - R7_QTD_QUEBRADA (heuristic)
  - R7_FIXO_VARIAVEL_ATIPICO (zscore por empreiteira-mês)
  - R7_LAG_EXECUCAO_PAGTO (zscore por empreiteira)

Cada handler é testado com:
  1. DB vazio → 0 findings (handler não emite)
  2. Dados controlados com 1 outlier conhecido → 1+ findings, score esperado

Seed direto em payments.wf_payment (não usa loader) — keep tight: cada
detector usa o subset mínimo que ativa sua técnica.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.adapters.db.postgres_payments import connect_payments, init_payments_schema
from app.core.domain.payments import AnalyticDetector, Severity, Technique
from app.core.services.payments.analytics import (
    AnalyticContext,
    ANALYTICS_REGISTRY,
)
from app.core.services.payments.analytics import _register_all  # noqa: F401


# ---------------------------------------------------------------------------
# Schema fixture + seed helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def _payments_schema():
    await init_payments_schema()


def _make_detector(code: str, technique: Technique, threshold: dict | None = None) -> AnalyticDetector:
    return AnalyticDetector(
        code=code,
        name=code,
        description="test",
        technique=technique,
        severity=Severity.MEDIUM,
        threshold_params=threshold or {},
        python_handler=f"app.core.services.payments.analytics.{code.lower()}",
    )


async def _bulk_insert_wf_payments(rows: list[dict]) -> None:
    """Insert helper para wf_payment. `rows` é list de dicts compatíveis
    com as colunas usadas pelos detectores (valor_unitario, valor_total_final,
    data_pedido, data_execucao, empreiteira, material_servico_num, os_num,
    mes_medicao, status_os, nivel_gerencial, malogro).
    """
    if not rows:
        return
    async with connect_payments() as c:
        await c.executemany(
            """
            INSERT INTO payments.wf_payment (
                os_num, sistema, empreiteira, data_pedido, data_execucao,
                valor_unitario, valor_total_final,
                material_servico_num, mes_medicao,
                status_os, nivel_gerencial, malogro
            ) VALUES (
                $1, 'WF1', $2, $3, $4, $5, $6, $7, $8,
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
                )
                for r in rows
            ],
        )


# ---------------------------------------------------------------------------
# R7_LPU_OUTLIER
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_lpu_outlier_empty_db_emits_nothing(_payments_schema):
    detector = _make_detector("R7_LPU_OUTLIER", Technique.IQR, {"min_samples": 5})
    ctx = AnalyticContext(detector=detector)
    handler = ANALYTICS_REGISTRY["R7_LPU_OUTLIER"]
    drafts = [d async for d in handler(ctx)]
    assert drafts == []


@pytest.mark.asyncio
async def test_r7_lpu_outlier_detects_iqr_outlier(_payments_schema):
    """30 payments com preço ≈ 100 + 1 outlier de 1000 (>>upper fence)."""
    rows = []
    for i in range(30):
        rows.append({
            "os_num": f"OS-{i:03d}",
            "empreiteira": "ENGEMAN MNT",
            "data_pedido": date(2025, 6, 1 + (i % 28)),
            "valor_unitario": Decimal(f"{100 + (i % 5)}"),  # 100-104, IQR estreita
            "valor_total_final": Decimal("100"),
            "material_servico_num": "SRV001",
        })
    # Outlier
    rows.append({
        "os_num": "OS-OUT",
        "empreiteira": "ENGEMAN MNT",
        "data_pedido": date(2025, 6, 29),
        "valor_unitario": Decimal("1000"),
        "valor_total_final": Decimal("1000"),
        "material_servico_num": "SRV001",
    })
    await _bulk_insert_wf_payments(rows)

    detector = _make_detector(
        "R7_LPU_OUTLIER", Technique.IQR,
        {"iqr_factor": 1.5, "min_samples": 10},
    )
    ctx = AnalyticContext(detector=detector)
    drafts = [d async for d in ANALYTICS_REGISTRY["R7_LPU_OUTLIER"](ctx)]

    # Exatamente 1 finding pro outlier.
    assert len(drafts) == 1
    d = drafts[0]
    assert d.detector_code == "R7_LPU_OUTLIER"
    assert d.actual_value["valor_unitario"] == 1000.0
    assert d.actual_value["material_servico_num"] == "SRV001"
    assert d.expected_range["method"] == "iqr"
    assert d.score > 100.0  # 1000 está >>1 IQR acima do Q3≈103


@pytest.mark.asyncio
async def test_r7_lpu_outlier_respects_min_samples(_payments_schema):
    """Grupo com <min_samples não dispara IQR (proteção a falsos positivos)."""
    rows = [
        {
            "os_num": f"OS-{i}", "empreiteira": "X",
            "data_pedido": date(2025, 6, i + 1),
            "valor_unitario": Decimal("100" if i < 4 else "10000"),
            "valor_total_final": Decimal("100"),
            "material_servico_num": "SRV_SMALL",
        }
        for i in range(5)
    ]
    await _bulk_insert_wf_payments(rows)

    detector = _make_detector(
        "R7_LPU_OUTLIER", Technique.IQR, {"min_samples": 50},
    )
    ctx = AnalyticContext(detector=detector)
    drafts = [d async for d in ANALYTICS_REGISTRY["R7_LPU_OUTLIER"](ctx)]
    assert drafts == []


# ---------------------------------------------------------------------------
# R7_QTD_QUEBRADA
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_qtd_quebrada_empty_db(_payments_schema):
    detector = _make_detector("R7_QTD_QUEBRADA", Technique.HEURISTIC)
    ctx = AnalyticContext(detector=detector)
    drafts = [d async for d in ANALYTICS_REGISTRY["R7_QTD_QUEBRADA"](ctx)]
    assert drafts == []


@pytest.mark.asyncio
async def test_r7_qtd_quebrada_flags_fractional_quantities(_payments_schema):
    """qtd = valor_total / valor_unitario. Casa decimal limpa (1.0) passa;
    quebrada (1.234) é flagada."""
    rows = [
        # qtd implícita = 5 / 5 = 1.0 → 0 casas → não flagado
        {"os_num": "OS-CLEAN", "empreiteira": "X", "data_pedido": date(2025, 1, 1),
         "valor_unitario": Decimal("5"), "valor_total_final": Decimal("5"),
         "material_servico_num": "S1"},
        # qtd implícita = 12.34 / 10 = 1.234 → 3 casas → flagado
        {"os_num": "OS-BROKEN", "empreiteira": "X", "data_pedido": date(2025, 1, 2),
         "valor_unitario": Decimal("10"), "valor_total_final": Decimal("12.34"),
         "material_servico_num": "S2"},
    ]
    await _bulk_insert_wf_payments(rows)

    detector = _make_detector(
        "R7_QTD_QUEBRADA", Technique.HEURISTIC,
        {"decimal_places_max": 2},
    )
    ctx = AnalyticContext(detector=detector)
    drafts = [d async for d in ANALYTICS_REGISTRY["R7_QTD_QUEBRADA"](ctx)]
    assert len(drafts) == 1
    d = drafts[0]
    assert d.actual_value["os_num"] == "OS-BROKEN"
    assert d.actual_value["decimal_places"] == 3
    assert d.score == 3.0


# ---------------------------------------------------------------------------
# R7_FIXO_VARIAVEL_ATIPICO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_fixo_variavel_empty_db(_payments_schema):
    detector = _make_detector("R7_FIXO_VARIAVEL_ATIPICO", Technique.ZSCORE)
    ctx = AnalyticContext(detector=detector)
    drafts = [d async for d in ANALYTICS_REGISTRY["R7_FIXO_VARIAVEL_ATIPICO"](ctx)]
    assert drafts == []


@pytest.mark.asyncio
async def test_r7_fixo_variavel_flags_anomalous_month(_payments_schema):
    """6 meses ≈ R$ 10k cada + 1 mês de R$ 100k. Esse último vira outlier."""
    base_months = ["2025/01", "2025/02", "2025/03", "2025/04", "2025/05", "2025/06"]
    rows = []
    for i, mes in enumerate(base_months):
        rows.append({
            "os_num": f"OS-NORMAL-{i}", "empreiteira": "EMP_TESTE",
            "data_pedido": date(2025, i + 1, 15),
            "valor_unitario": Decimal("100"), "valor_total_final": Decimal("10000"),
            "mes_medicao": mes,
        })
    # Outlier
    rows.append({
        "os_num": "OS-SPIKE", "empreiteira": "EMP_TESTE",
        "data_pedido": date(2025, 7, 15),
        "valor_unitario": Decimal("100"), "valor_total_final": Decimal("100000"),
        "mes_medicao": "2025/07",
    })
    await _bulk_insert_wf_payments(rows)

    detector = _make_detector(
        "R7_FIXO_VARIAVEL_ATIPICO", Technique.ZSCORE,
        {"zscore_threshold": 2.0, "min_months": 6},
    )
    ctx = AnalyticContext(detector=detector)
    drafts = [d async for d in ANALYTICS_REGISTRY["R7_FIXO_VARIAVEL_ATIPICO"](ctx)]
    assert len(drafts) >= 1
    spike = next((d for d in drafts if d.actual_value["mes_medicao"] == "2025/07"), None)
    assert spike is not None
    assert spike.score > 2.0
    assert spike.actual_value["empreiteira"] == "EMP_TESTE"


# ---------------------------------------------------------------------------
# R7_LAG_EXECUCAO_PAGTO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_lag_pagto_empty_db(_payments_schema):
    detector = _make_detector("R7_LAG_EXECUCAO_PAGTO", Technique.ZSCORE)
    ctx = AnalyticContext(detector=detector)
    drafts = [d async for d in ANALYTICS_REGISTRY["R7_LAG_EXECUCAO_PAGTO"](ctx)]
    assert drafts == []


@pytest.mark.asyncio
async def test_r7_lag_pagto_flags_anomalous_lag(_payments_schema):
    """20 payments com lag ≈ 5 dias + 1 com lag 100 dias.

    Para a distribuição [5..5..5, 100], z do 100 fica alto.
    """
    rows = []
    for i in range(20):
        d_ped = date(2025, 6, 1)
        d_exec = date(2025, 6, 6)  # lag = 5
        rows.append({
            "os_num": f"OS-{i:03d}", "empreiteira": "EMP_LAG",
            "data_pedido": d_ped, "data_execucao": d_exec,
            "valor_unitario": Decimal("100"), "valor_total_final": Decimal("100"),
            "material_servico_num": "SRV",
        })
    rows.append({
        "os_num": "OS-SLOW", "empreiteira": "EMP_LAG",
        "data_pedido": date(2025, 6, 1), "data_execucao": date(2025, 9, 9),  # lag=100
        "valor_unitario": Decimal("100"), "valor_total_final": Decimal("100"),
        "material_servico_num": "SRV",
    })
    await _bulk_insert_wf_payments(rows)

    detector = _make_detector(
        "R7_LAG_EXECUCAO_PAGTO", Technique.ZSCORE,
        {"zscore_threshold": 2.0, "min_samples": 10},
    )
    ctx = AnalyticContext(detector=detector)
    drafts = [d async for d in ANALYTICS_REGISTRY["R7_LAG_EXECUCAO_PAGTO"](ctx)]
    assert len(drafts) == 1
    d = drafts[0]
    assert d.actual_value["lag_dias"] == 100
    assert d.actual_value["os_num"] == "OS-SLOW"
    assert d.score > 2.0

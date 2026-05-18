"""Tests dos 4 detectores complexos (Bloco D):

  - R7_EMPREITEIRA_OUT_PADRAO (Z-score por categoria)
  - R7_RECORR_VARIAVEL (count threshold em tipo_de_lpu='LPU MEDIÇÃO')
  - R7_CONSUMO_PERFIL (Z-score por bucket de n_regionais)
  - R7_LPU_PADRAO_SERVICO (IQR por atividade+categoria)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.adapters.db.postgres_payments import connect_payments, init_payments_schema
from app.core.domain.payments import AnalyticDetector, Severity, Technique
from app.core.services.payments.analytics import (
    ANALYTICS_REGISTRY,
    AnalyticContext,
)
from app.core.services.payments.analytics import _register_all  # noqa: F401


@pytest.fixture
async def _payments_schema():
    await init_payments_schema()


def _make_detector(
    code: str, technique: Technique, threshold: dict | None = None
) -> AnalyticDetector:
    return AnalyticDetector(
        code=code,
        name=code,
        description="test",
        technique=technique,
        severity=Severity.MEDIUM,
        threshold_params=threshold or {},
        python_handler=f"app.core.services.payments.analytics.{code.lower()}",
    )


async def _insert_wide(rows: list[dict]) -> None:
    """Insert helper extendido pros detectores complexos — aceita categoria,
    atividade, tipo_de_lpu, regional_soe_nova além das colunas básicas."""
    if not rows:
        return
    async with connect_payments() as c:
        await c.executemany(
            """
            INSERT INTO payments.wf_payment (
                os_num, sistema, empreiteira, data_pedido,
                valor_unitario, valor_total_final,
                material_servico_num,
                categoria, atividade, tipo_de_lpu, regional_soe_nova,
                status_os, nivel_gerencial, malogro
            ) VALUES (
                $1, 'WF1', $2, $3, $4, $5, $6, $7, $8, $9, $10,
                'EXECUTADO', 'Em Pagamento', 'OK'
            )
            """,
            [
                (
                    r["os_num"], r["empreiteira"], r["data_pedido"],
                    r.get("valor_unitario"), r.get("valor_total_final"),
                    r.get("material_servico_num"),
                    r.get("categoria"), r.get("atividade"),
                    r.get("tipo_de_lpu"), r.get("regional_soe_nova"),
                )
                for r in rows
            ],
        )


# ---------------------------------------------------------------------------
# R7_EMPREITEIRA_OUT_PADRAO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_empreiteira_padrao_empty(_payments_schema):
    d = _make_detector("R7_EMPREITEIRA_OUT_PADRAO", Technique.CLUSTERING)
    drafts = [
        x async for x in ANALYTICS_REGISTRY["R7_EMPREITEIRA_OUT_PADRAO"](
            AnalyticContext(detector=d)
        )
    ]
    assert drafts == []


@pytest.mark.asyncio
async def test_r7_empreiteira_padrao_flags_outlier_in_category(_payments_schema):
    """6 empreiteiras na categoria INSTALACAO com preço médio ≈ 100 + 1
    com preço médio 1000 (>>peer mean)."""
    rows = []
    for i in range(6):
        rows.append({
            "os_num": f"OS-N-{i}", "empreiteira": f"EMP_NORMAL_{i}",
            "data_pedido": date(2025, 6, 1),
            "valor_unitario": Decimal("100"), "valor_total_final": Decimal("100"),
            "categoria": "INSTALACAO",
        })
    rows.append({
        "os_num": "OS-OUT", "empreiteira": "EMP_OUTLIER",
        "data_pedido": date(2025, 6, 2),
        "valor_unitario": Decimal("1000"), "valor_total_final": Decimal("1000"),
        "categoria": "INSTALACAO",
    })
    await _insert_wide(rows)

    d = _make_detector(
        "R7_EMPREITEIRA_OUT_PADRAO", Technique.CLUSTERING,
        {"isolation_threshold": 1.0, "min_pairs": 5},
    )
    drafts = [
        x async for x in ANALYTICS_REGISTRY["R7_EMPREITEIRA_OUT_PADRAO"](
            AnalyticContext(detector=d)
        )
    ]
    outlier = next(
        (x for x in drafts if x.actual_value["empreiteira"] == "EMP_OUTLIER"), None
    )
    assert outlier is not None
    assert abs(outlier.score) > 1.0
    assert outlier.actual_value["categoria"] == "INSTALACAO"


# ---------------------------------------------------------------------------
# R7_RECORR_VARIAVEL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_recorr_variavel_empty(_payments_schema):
    d = _make_detector("R7_RECORR_VARIAVEL", Technique.HEURISTIC)
    drafts = [
        x async for x in ANALYTICS_REGISTRY["R7_RECORR_VARIAVEL"](
            AnalyticContext(detector=d)
        )
    ]
    assert drafts == []


@pytest.mark.asyncio
async def test_r7_recorr_variavel_flags_excess_recurrences(_payments_schema):
    """15 occorrências do mesmo material_servico com tipo_de_lpu='LPU MEDIÇÃO'
    para a mesma empreiteira (>12 default)."""
    rows = []
    for i in range(15):
        rows.append({
            "os_num": f"OS-{i:03d}", "empreiteira": "EMP_REC",
            "data_pedido": date(2025, 1, 1 + (i % 28)),
            "valor_unitario": Decimal("10"), "valor_total_final": Decimal("100"),
            "material_servico_num": "SRV_REC",
            "tipo_de_lpu": "LPU MEDIÇÃO",
        })
    await _insert_wide(rows)

    d = _make_detector(
        "R7_RECORR_VARIAVEL", Technique.HEURISTIC,
        {"max_recurrences": 12, "lookback_months": 24},
    )
    drafts = [
        x async for x in ANALYTICS_REGISTRY["R7_RECORR_VARIAVEL"](
            AnalyticContext(detector=d)
        )
    ]
    assert len(drafts) == 1
    d0 = drafts[0]
    assert d0.actual_value["empreiteira"] == "EMP_REC"
    assert d0.actual_value["material_servico_num"] == "SRV_REC"
    assert d0.actual_value["n_recorrencias"] == 15
    assert len(d0.evidence_payment_ids) == 15


# ---------------------------------------------------------------------------
# R7_CONSUMO_PERFIL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_consumo_perfil_empty(_payments_schema):
    d = _make_detector("R7_CONSUMO_PERFIL", Technique.ZSCORE)
    drafts = [
        x async for x in ANALYTICS_REGISTRY["R7_CONSUMO_PERFIL"](
            AnalyticContext(detector=d)
        )
    ]
    assert drafts == []


@pytest.mark.asyncio
async def test_r7_consumo_perfil_flags_outlier_in_regional_bucket(_payments_schema):
    """3 empreiteiras que atuam em 1 regional com total ≈ R$ 1k + 1 com
    R$ 100k. Outlier flagado."""
    rows = []
    for i, name in enumerate(["A", "B", "C"]):
        rows.append({
            "os_num": f"OS-{name}", "empreiteira": f"EMP_{name}",
            "data_pedido": date(2025, 6, 1 + i),
            "valor_unitario": Decimal("10"), "valor_total_final": Decimal("1000"),
            "regional_soe_nova": "SP",
        })
    rows.append({
        "os_num": "OS-OUT", "empreiteira": "EMP_OUT",
        "data_pedido": date(2025, 6, 10),
        "valor_unitario": Decimal("1000"), "valor_total_final": Decimal("100000"),
        "regional_soe_nova": "SP",
    })
    await _insert_wide(rows)

    d = _make_detector(
        "R7_CONSUMO_PERFIL", Technique.ZSCORE,
        {"zscore_threshold": 1.0, "min_peers": 3},
    )
    drafts = [
        x async for x in ANALYTICS_REGISTRY["R7_CONSUMO_PERFIL"](
            AnalyticContext(detector=d)
        )
    ]
    outlier = next(
        (x for x in drafts if x.actual_value["empreiteira"] == "EMP_OUT"), None
    )
    assert outlier is not None
    assert outlier.actual_value["n_regionais"] == 1


# ---------------------------------------------------------------------------
# R7_LPU_PADRAO_SERVICO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_lpu_padrao_servico_empty(_payments_schema):
    d = _make_detector("R7_LPU_PADRAO_SERVICO", Technique.IQR)
    drafts = [
        x async for x in ANALYTICS_REGISTRY["R7_LPU_PADRAO_SERVICO"](
            AnalyticContext(detector=d)
        )
    ]
    assert drafts == []


@pytest.mark.asyncio
async def test_r7_lpu_padrao_servico_detects_iqr_outlier(_payments_schema):
    """30 payments em (atividade=ESCAVACAO, categoria=OBRA) com preço≈100 +
    1 outlier de 1000."""
    rows = []
    for i in range(30):
        rows.append({
            "os_num": f"OS-{i:03d}", "empreiteira": "EMP",
            "data_pedido": date(2025, 6, 1 + (i % 28)),
            "valor_unitario": Decimal(f"{100 + (i % 5)}"),
            "valor_total_final": Decimal("100"),
            "material_servico_num": f"M{i}",
            "atividade": "ESCAVACAO", "categoria": "OBRA",
        })
    rows.append({
        "os_num": "OS-OUT", "empreiteira": "EMP",
        "data_pedido": date(2025, 7, 1),
        "valor_unitario": Decimal("1000"), "valor_total_final": Decimal("1000"),
        "material_servico_num": "M-OUT",
        "atividade": "ESCAVACAO", "categoria": "OBRA",
    })
    await _insert_wide(rows)

    d = _make_detector(
        "R7_LPU_PADRAO_SERVICO", Technique.IQR,
        {"iqr_factor": 1.5, "min_samples": 10},
    )
    drafts = [
        x async for x in ANALYTICS_REGISTRY["R7_LPU_PADRAO_SERVICO"](
            AnalyticContext(detector=d)
        )
    ]
    assert len(drafts) == 1
    d0 = drafts[0]
    assert d0.actual_value["material_servico_num"] == "M-OUT"
    assert d0.actual_value["atividade"] == "ESCAVACAO"
    assert d0.score > 50  # bem fora do IQR

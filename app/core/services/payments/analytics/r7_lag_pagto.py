"""R7_LAG_EXECUCAO_PAGTO — intervalo anormal entre execução e pedido
(técnica Z-score por empreiteira).

`lag_dias = data_execucao - data_pedido` (negativo se execução precede
pedido — raro mas possível). Para cada empreiteira, calcula a distribuição
de lags e flag outliers individuais.

Parâmetros:
  zscore_threshold: float — corte (default 2.0)
  min_samples: int       — mínimo de pagamentos pra calcular zscore (default 20)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator

from app.adapters.db.postgres_payments import connect_payments
from app.core.domain.payments import Severity
from app.core.services.payments.analytics import register
from app.core.services.payments.analytics._base import (
    AnalyticContext,
    AnalyticFindingDraft,
    universe_filter_for_detector,
)
from app.core.services.payments.analytics._stats import (
    mean,
    stdev,
    zscore,
)


@register("R7_LAG_EXECUCAO_PAGTO")
async def r7_lag_pagto(ctx: AnalyticContext) -> AsyncIterator[AnalyticFindingDraft]:
    threshold = float(ctx.detector.threshold_params.get("zscore_threshold", 2.0))
    min_samples = int(ctx.detector.threshold_params.get("min_samples", 20))
    universe = universe_filter_for_detector(ctx.detector)
    where_clause = (
        f"WHERE {universe} AND data_execucao IS NOT NULL"
        if universe
        else "WHERE data_execucao IS NOT NULL"
    )

    async with connect_payments() as c:
        rows = await c.fetch(
            f"""
            SELECT
                wp.id, wp.empreiteira, wp.os_num,
                wp.data_pedido, wp.data_execucao,
                (wp.data_execucao - wp.data_pedido) AS lag_dias,
                sb.id AS supplier_id
            FROM payments.wf_payment wp
            LEFT JOIN payments.supplier_bridge sb ON sb.empreiteira = wp.empreiteira
            {where_clause}
            """
        )

    # Agrupa por empreiteira em memória.
    by_empreiteira: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_empreiteira[r["empreiteira"]].append(
            {
                "id": int(r["id"]),
                "os": r["os_num"],
                "lag": int(r["lag_dias"]) if r["lag_dias"] is not None else None,
                "data_pedido": r["data_pedido"],
                "supplier_id": r["supplier_id"],
            }
        )

    for empreiteira, payments in by_empreiteira.items():
        lags = [p["lag"] for p in payments if p["lag"] is not None]
        if len(lags) < min_samples:
            continue
        mu = mean(lags)
        sd = stdev(lags)
        if sd == 0.0:
            continue
        for p in payments:
            if p["lag"] is None:
                continue
            z = zscore(p["lag"], lags)
            if abs(z) <= threshold:
                continue
            yield AnalyticFindingDraft(
                detector_code="R7_LAG_EXECUCAO_PAGTO",
                severity=Severity(ctx.detector.severity.value),
                score=float(z),
                expected_range={
                    "mean_dias": mu,
                    "stdev_dias": sd,
                    "zscore_threshold": threshold,
                    "method": "zscore",
                    "n_samples": len(lags),
                },
                actual_value={
                    "lag_dias": p["lag"],
                    "empreiteira": empreiteira,
                    "os_num": p["os"],
                },
                wf_payment_id=p["id"],
                wf_payment_data_pedido=p["data_pedido"],
                supplier_id=p["supplier_id"],
                reason=(
                    f"{empreiteira} OS {p['os']}: lag {p['lag']}d "
                    f"(z={z:+.2f}, μ={mu:.1f}d, σ={sd:.1f}d)"
                ),
            )

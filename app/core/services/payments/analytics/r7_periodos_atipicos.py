"""R7_PERIODOS_ATIPICOS — concentração de pagamentos em períodos atípicos
(técnica timeseries_outlier).

Por empreiteira, distribui pagamentos por mês do calendário (1-12) e
emite finding quando algum mês concentra valor anormalmente acima da
média histórica (Z-score sobre os 12 totais mensais).

Parâmetros:
  zscore_threshold: float — corte (default 2.0)
  min_months_distinct: int — mínimo de meses distintos (default 6)
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
from app.core.services.payments.analytics._stats import mean, stdev, zscore


@register("R7_PERIODOS_ATIPICOS")
async def r7_periodos_atipicos(
    ctx: AnalyticContext,
) -> AsyncIterator[AnalyticFindingDraft]:
    threshold = float(ctx.detector.threshold_params.get("zscore_threshold", 2.0))
    min_months = int(ctx.detector.threshold_params.get("min_months_distinct", 6))
    universe = universe_filter_for_detector(ctx.detector)
    where_clause = (
        f"WHERE {universe} AND valor_total_final IS NOT NULL"
        if universe
        else "WHERE valor_total_final IS NOT NULL"
    )

    async with connect_payments() as c:
        rows = await c.fetch(
            f"""
            SELECT
                wp.empreiteira,
                EXTRACT(MONTH FROM wp.data_pedido)::int AS mes_calendario,
                SUM(wp.valor_total_final) AS total_mes,
                MAX(wp.data_pedido)        AS data_ref,
                sb.id AS supplier_id
            FROM payments.wf_payment wp
            LEFT JOIN payments.supplier_bridge sb ON sb.empreiteira = wp.empreiteira
            {where_clause}
            GROUP BY wp.empreiteira, mes_calendario, sb.id
            ORDER BY wp.empreiteira, mes_calendario
            """
        )

    by_empreiteira: dict = defaultdict(list)
    for r in rows:
        by_empreiteira[r["empreiteira"]].append(
            {
                "mes": int(r["mes_calendario"]),
                "total": float(r["total_mes"] or 0),
                "data_ref": r["data_ref"],
                "supplier_id": r["supplier_id"],
            }
        )

    for empreiteira, meses in by_empreiteira.items():
        if len(meses) < min_months:
            continue
        totals = [m["total"] for m in meses]
        mu = mean(totals)
        sd = stdev(totals)
        if sd == 0.0:
            continue
        for m in meses:
            z = zscore(m["total"], totals)
            if abs(z) <= threshold:
                continue
            yield AnalyticFindingDraft(
                detector_code="R7_PERIODOS_ATIPICOS",
                severity=Severity(ctx.detector.severity.value),
                score=float(z),
                expected_range={
                    "mean_mensal": mu,
                    "stdev_mensal": sd,
                    "zscore_threshold": threshold,
                    "method": "zscore_calendar",
                    "months_observed": len(meses),
                },
                actual_value={
                    "mes_calendario": m["mes"],
                    "total_mes": m["total"],
                    "empreiteira": empreiteira,
                },
                wf_payment_data_pedido=m["data_ref"],
                supplier_id=m["supplier_id"],
                reason=(
                    f"{empreiteira} mês {m['mes']:02d}: R$ {m['total']:.2f} "
                    f"(z={z:+.2f} vs μ {mu:.2f})"
                ),
            )

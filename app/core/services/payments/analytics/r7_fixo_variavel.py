"""R7_FIXO_VARIAVEL_ATIPICO — variações atípicas em valores mensais
(técnica Z-score).

Por empreiteira, agrega `valor_total_final` somado por `mes_medicao`. Z-score
de cada mês contra a distribuição histórica da mesma empreiteira sinaliza
meses fora do padrão (picos ou quedas).

Não tenta separar FIXO vs VARIÁVEL via `tipo_de_lpu` no nível atual — a
flag é sobre a TOTALIDADE mensal, independente do tipo. Granularidade
ajustável no futuro via threshold_params (group_by=['tipo_de_lpu']).

Parâmetros:
  zscore_threshold: float — corte (default 2.0)
  min_months: int        — mínimo de meses pra calcular zscore (default 6)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from decimal import Decimal

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


@register("R7_FIXO_VARIAVEL_ATIPICO")
async def r7_fixo_variavel(ctx: AnalyticContext) -> AsyncIterator[AnalyticFindingDraft]:
    threshold = float(ctx.detector.threshold_params.get("zscore_threshold", 2.0))
    min_months = int(ctx.detector.threshold_params.get("min_months", 6))
    universe = universe_filter_for_detector(ctx.detector)
    where_clause = (
        f"WHERE {universe} AND mes_medicao IS NOT NULL "
        "AND valor_total_final IS NOT NULL"
        if universe
        else "WHERE mes_medicao IS NOT NULL AND valor_total_final IS NOT NULL"
    )

    async with connect_payments() as c:
        rows = await c.fetch(
            f"""
            SELECT
                wp.empreiteira,
                wp.mes_medicao,
                SUM(wp.valor_total_final) AS total_mes,
                MAX(wp.data_pedido)        AS data_ref,
                sb.id AS supplier_id
            FROM payments.wf_payment wp
            LEFT JOIN payments.supplier_bridge sb ON sb.empreiteira = wp.empreiteira
            {where_clause}
            GROUP BY wp.empreiteira, wp.mes_medicao, sb.id
            ORDER BY wp.empreiteira, wp.mes_medicao
            """
        )

    # Agrupa em memória por empreiteira → lista de (mes, total, data_ref, supplier).
    by_empreiteira: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_empreiteira[r["empreiteira"]].append(
            {
                "mes": r["mes_medicao"],
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
                detector_code="R7_FIXO_VARIAVEL_ATIPICO",
                severity=Severity(ctx.detector.severity.value),
                score=float(z),
                expected_range={
                    "mean": mu,
                    "stdev": sd,
                    "zscore_threshold": threshold,
                    "method": "zscore",
                    "n_months": len(meses),
                },
                actual_value={
                    "mes_medicao": m["mes"],
                    "total_mes": m["total"],
                    "empreiteira": empreiteira,
                },
                wf_payment_data_pedido=m["data_ref"],
                supplier_id=m["supplier_id"],
                reason=(
                    f"{empreiteira} mês {m['mes']} R$ {m['total']:.2f} "
                    f"(z={z:+.2f}, μ={mu:.2f}, σ={sd:.2f})"
                ),
            )

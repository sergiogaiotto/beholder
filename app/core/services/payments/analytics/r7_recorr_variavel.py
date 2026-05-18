"""R7_RECORR_VARIAVEL — recorrência excessiva de serviços variáveis (heurística).

`tipo_de_lpu = 'LPU MEDIÇÃO'` denota serviço variável — por definição
medido caso a caso, sem ser fixo mensal. Quando o MESMO material_servico
aparece com tipo_de_lpu='LPU MEDIÇÃO' acima de `max_recurrences` vezes
para a mesma empreiteira, é sinal de que o "variável" virou recorrente —
deveria ser repactuado como fixo.

Parâmetros:
  max_recurrences: int   — limite (default 12 — ≈mensal por ano)
  lookback_months: int   — janela (default 12)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta

from app.adapters.db.postgres_payments import connect_payments
from app.core.domain.payments import Severity
from app.core.services.payments.analytics import register
from app.core.services.payments.analytics._base import (
    AnalyticContext,
    AnalyticFindingDraft,
    universe_filter_for_detector,
)


@register("R7_RECORR_VARIAVEL")
async def r7_recorr_variavel(
    ctx: AnalyticContext,
) -> AsyncIterator[AnalyticFindingDraft]:
    max_rec = int(ctx.detector.threshold_params.get("max_recurrences", 12))
    lookback_months = int(ctx.detector.threshold_params.get("lookback_months", 12))
    universe = universe_filter_for_detector(ctx.detector)
    cutoff = date.today() - timedelta(days=30 * lookback_months)
    where_clause = (
        f"WHERE {universe} AND tipo_de_lpu = 'LPU MEDIÇÃO' "
        f"AND material_servico_num IS NOT NULL "
        f"AND data_pedido >= $1"
        if universe
        else "WHERE tipo_de_lpu = 'LPU MEDIÇÃO' "
        "AND material_servico_num IS NOT NULL AND data_pedido >= $1"
    )

    async with connect_payments() as c:
        rows = await c.fetch(
            f"""
            SELECT
                wp.empreiteira,
                wp.material_servico_num,
                COUNT(*) AS n_recorrencias,
                ARRAY_AGG(wp.id ORDER BY wp.data_pedido) AS payment_ids,
                MAX(wp.data_pedido) AS data_ref,
                sb.id AS supplier_id
            FROM payments.wf_payment wp
            LEFT JOIN payments.supplier_bridge sb ON sb.empreiteira = wp.empreiteira
            {where_clause}
            GROUP BY wp.empreiteira, wp.material_servico_num, sb.id
            HAVING COUNT(*) > $2
            """,
            cutoff, max_rec,
        )

    for r in rows:
        yield AnalyticFindingDraft(
            detector_code="R7_RECORR_VARIAVEL",
            severity=Severity(ctx.detector.severity.value),
            score=float(r["n_recorrencias"]),
            expected_range={
                "max_recurrences": max_rec,
                "lookback_months": lookback_months,
                "method": "count_threshold",
            },
            actual_value={
                "empreiteira": r["empreiteira"],
                "material_servico_num": r["material_servico_num"],
                "n_recorrencias": int(r["n_recorrencias"]),
            },
            evidence_payment_ids=[int(p) for p in r["payment_ids"]],
            wf_payment_data_pedido=r["data_ref"],
            supplier_id=r["supplier_id"],
            reason=(
                f"{r['empreiteira']} usou material {r['material_servico_num']} "
                f"como LPU MEDIÇÃO {r['n_recorrencias']}× em {lookback_months} meses "
                f"(>{max_rec})"
            ),
        )

"""R7_LPU_PADRAO_SERVICO — LPU fora do padrão para a atividade (IQR por
atividade+categoria).

Variante do LPU_OUTLIER que agrupa por (`atividade`, `categoria`) em vez
de `material_servico_num`. Mais permissiva — flag preços anormais ao
nível de serviço/escopo, não de SKU. Útil para descobrir
material_servico raramente usados mas com preço fora do segmento.

Parâmetros iguais ao LPU_OUTLIER (iqr_factor, min_samples).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.adapters.db.postgres_payments import connect_payments
from app.core.domain.payments import Severity
from app.core.services.payments.analytics import register
from app.core.services.payments.analytics._base import (
    AnalyticContext,
    AnalyticFindingDraft,
    universe_filter_for_detector,
)


@register("R7_LPU_PADRAO_SERVICO")
async def r7_lpu_padrao_servico(
    ctx: AnalyticContext,
) -> AsyncIterator[AnalyticFindingDraft]:
    factor = float(ctx.detector.threshold_params.get("iqr_factor", 1.5))
    min_samples = int(ctx.detector.threshold_params.get("min_samples", 30))
    universe = universe_filter_for_detector(ctx.detector)
    universe_clause = f"WHERE {universe}" if universe else ""

    sql = f"""
        WITH bounds AS (
            SELECT
                atividade, categoria,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY valor_unitario) AS q1,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY valor_unitario) AS q3,
                COUNT(*) AS n
            FROM payments.wf_payment
            {universe_clause}
            {'AND' if universe_clause else 'WHERE'} atividade IS NOT NULL
              AND categoria IS NOT NULL
              AND valor_unitario IS NOT NULL
            GROUP BY atividade, categoria
            HAVING COUNT(*) >= $1
        )
        SELECT
            wp.id, wp.data_pedido, wp.empreiteira, wp.os_num,
            wp.atividade, wp.categoria, wp.material_servico_num,
            wp.valor_unitario,
            b.q1, b.q3, b.n,
            sb.id AS supplier_id
        FROM payments.wf_payment wp
        JOIN bounds b
          ON b.atividade = wp.atividade AND b.categoria = wp.categoria
        LEFT JOIN payments.supplier_bridge sb ON sb.empreiteira = wp.empreiteira
        {universe_clause.replace('payments.wf_payment', 'wp')}
        {'AND' if universe_clause else 'WHERE'} wp.atividade IS NOT NULL
          AND wp.categoria IS NOT NULL
          AND wp.valor_unitario IS NOT NULL
          AND (
              wp.valor_unitario < (b.q1 - (b.q3 - b.q1) * $2)
              OR wp.valor_unitario > (b.q3 + (b.q3 - b.q1) * $2)
          )
    """

    async with connect_payments() as c:
        rows = await c.fetch(sql, min_samples, factor)

    for r in rows:
        q1 = float(r["q1"])
        q3 = float(r["q3"])
        iqr = q3 - q1
        lower = q1 - factor * iqr
        upper = q3 + factor * iqr
        actual = float(r["valor_unitario"])
        if iqr == 0.0:
            continue
        distance_iqr = (
            (actual - q3) / iqr if actual > upper else (actual - q1) / iqr
        )
        yield AnalyticFindingDraft(
            detector_code="R7_LPU_PADRAO_SERVICO",
            severity=Severity(ctx.detector.severity.value),
            score=float(distance_iqr),
            expected_range={
                "min": lower, "max": upper,
                "q1": q1, "q3": q3,
                "atividade": r["atividade"],
                "categoria": r["categoria"],
                "method": "iqr_per_activity",
                "n_samples": int(r["n"]),
            },
            actual_value={
                "valor_unitario": actual,
                "atividade": r["atividade"],
                "categoria": r["categoria"],
                "empreiteira": r["empreiteira"],
                "material_servico_num": r["material_servico_num"],
                "os_num": r["os_num"],
            },
            wf_payment_id=int(r["id"]),
            wf_payment_data_pedido=r["data_pedido"],
            supplier_id=r["supplier_id"],
            reason=(
                f"OS {r['os_num']}: R$ {actual} fora [{lower:.2f}, {upper:.2f}] "
                f"({r['atividade']} / {r['categoria']}, n={r['n']})"
            ),
        )

"""R7_LPU_OUTLIER — preços fora do padrão para o mesmo serviço (técnica IQR).

Agrupa wf_payment por `material_servico_num` e usa o método clássico de
Tukey (Q1/Q3 ± factor·IQR) para flagar preços muito acima ou abaixo do
padrão histórico do mesmo serviço.

Parâmetros (detector.threshold_params):
  iqr_factor: float    — multiplicador da IQR (default 1.5; mais permissivo)
  min_samples: int     — amostras mínimas no grupo antes de calcular IQR
                         (default 30). Grupos menores são ignorados.

Score do finding: distância em IQRs da fence cruzada — positivo se acima do
upper, negativo se abaixo do lower. Ex.: 2.5 ≈ preço pago é 2.5× IQR acima
do Q3, claramente fora do padrão.

PERCENTILE_CONT roda no Postgres — evita transferir GB de wf_payment ao
Python pra IQR. O Python só consome os outliers já filtrados.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

from app.adapters.db.postgres_payments import connect_payments
from app.core.domain.payments import Severity
from app.core.services.payments.analytics._base import (
    AnalyticContext,
    AnalyticFindingDraft,
    universe_filter_for_detector,
)
from app.core.services.payments.analytics import register


@register("R7_LPU_OUTLIER")
async def r7_lpu_outlier(ctx: AnalyticContext) -> AsyncIterator[AnalyticFindingDraft]:
    factor = float(ctx.detector.threshold_params.get("iqr_factor", 1.5))
    min_samples = int(ctx.detector.threshold_params.get("min_samples", 30))
    universe = universe_filter_for_detector(ctx.detector)
    universe_clause = f"WHERE {universe}" if universe else ""

    sql = f"""
        WITH bounds AS (
            SELECT
                material_servico_num,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY valor_unitario) AS q1,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY valor_unitario) AS q3,
                COUNT(*) AS n
            FROM payments.wf_payment
            {universe_clause}
            {'AND' if universe_clause else 'WHERE'} material_servico_num IS NOT NULL
              AND valor_unitario IS NOT NULL
            GROUP BY material_servico_num
            HAVING COUNT(*) >= $1
        )
        SELECT
            wp.id, wp.data_pedido, wp.empreiteira, wp.material_servico_num,
            wp.valor_unitario, wp.os_num,
            b.q1, b.q3, b.n,
            sb.id AS supplier_id
        FROM payments.wf_payment wp
        JOIN bounds b ON b.material_servico_num = wp.material_servico_num
        LEFT JOIN payments.supplier_bridge sb ON sb.empreiteira = wp.empreiteira
        {universe_clause.replace('payments.wf_payment', 'wp')}
        {'AND' if universe_clause else 'WHERE'} wp.material_servico_num IS NOT NULL
          AND wp.valor_unitario IS NOT NULL
          AND (
              wp.valor_unitario < (b.q1 - (b.q3 - b.q1) * $2)
              OR wp.valor_unitario > (b.q3 + (b.q3 - b.q1) * $2)
          )
    """
    # WHERE re-aplicado dentro de wp já usa o universe_clause original;
    # mas substituir 'payments.wf_payment' deixa ele apontando a `wp` —
    # PostgreSQL aceita o alias.

    async with connect_payments() as c:
        rows = await c.fetch(sql, min_samples, factor)

    for r in rows:
        q1 = float(r["q1"])
        q3 = float(r["q3"])
        iqr = q3 - q1
        lower = q1 - factor * iqr
        upper = q3 + factor * iqr
        actual = float(r["valor_unitario"])
        if actual > upper:
            distance_iqr = (actual - q3) / iqr if iqr else 0.0
        else:
            distance_iqr = (actual - q1) / iqr if iqr else 0.0
        yield AnalyticFindingDraft(
            detector_code="R7_LPU_OUTLIER",
            severity=Severity(ctx.detector.severity.value),
            score=float(distance_iqr),
            expected_range={
                "min": lower,
                "max": upper,
                "q1": q1,
                "q3": q3,
                "method": "iqr",
                "factor": factor,
                "n_samples": int(r["n"]),
            },
            actual_value={
                "valor_unitario": actual,
                "material_servico_num": r["material_servico_num"],
                "empreiteira": r["empreiteira"],
                "os_num": r["os_num"],
            },
            wf_payment_id=int(r["id"]),
            wf_payment_data_pedido=r["data_pedido"],
            supplier_id=r["supplier_id"],
            reason=(
                f"valor_unitario {actual} fora de [{lower:.2f}, {upper:.2f}] "
                f"para {r['material_servico_num']} (n={r['n']})"
            ),
        )

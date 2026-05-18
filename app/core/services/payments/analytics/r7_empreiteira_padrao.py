"""R7_EMPREITEIRA_OUT_PADRAO — empreiteira fora do padrão dos pares
(técnica clustering — proxy via Z-score por segmento).

Para cada `categoria`, calcula preço médio (média de valor_unitario) por
empreiteira. Empreiteira cujo preço médio destoa do peer group da mesma
categoria (|Z-score| > isolation_threshold) é flagada.

Decisão: começar com Z-score por segmento como proxy de Isolation Forest /
DBSCAN. Reduz dependência (puro Python) e mantém o sinal interpretável.
Migrar para sklearn no futuro se ROI justificar.

Parâmetros:
  isolation_threshold: float — corte de |Z| (default 0.7)
  min_pairs: int             — mínimo de empreiteiras na categoria (default 5)
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


@register("R7_EMPREITEIRA_OUT_PADRAO")
async def r7_empreiteira_padrao(
    ctx: AnalyticContext,
) -> AsyncIterator[AnalyticFindingDraft]:
    threshold = float(ctx.detector.threshold_params.get("isolation_threshold", 0.7))
    min_pairs = int(ctx.detector.threshold_params.get("min_pairs", 5))
    universe = universe_filter_for_detector(ctx.detector)
    where_clause = (
        f"WHERE {universe} AND wp.categoria IS NOT NULL AND wp.valor_unitario IS NOT NULL"
        if universe
        else "WHERE wp.categoria IS NOT NULL AND wp.valor_unitario IS NOT NULL"
    )

    async with connect_payments() as c:
        rows = await c.fetch(
            f"""
            SELECT
                wp.categoria,
                wp.empreiteira,
                AVG(wp.valor_unitario)::numeric AS avg_unit,
                MAX(wp.data_pedido) AS data_ref,
                sb.id AS supplier_id
            FROM payments.wf_payment wp
            LEFT JOIN payments.supplier_bridge sb ON sb.empreiteira = wp.empreiteira
            {where_clause}
            GROUP BY wp.categoria, wp.empreiteira, sb.id
            """
        )

    by_cat: dict = defaultdict(list)
    for r in rows:
        by_cat[r["categoria"]].append(
            {
                "empreiteira": r["empreiteira"],
                "avg_unit": float(r["avg_unit"]),
                "data_ref": r["data_ref"],
                "supplier_id": r["supplier_id"],
            }
        )

    for categoria, peers in by_cat.items():
        if len(peers) < min_pairs:
            continue
        avgs = [p["avg_unit"] for p in peers]
        sd = stdev(avgs)
        if sd == 0.0:
            continue
        mu = mean(avgs)
        for p in peers:
            z = zscore(p["avg_unit"], avgs)
            if abs(z) <= threshold:
                continue
            yield AnalyticFindingDraft(
                detector_code="R7_EMPREITEIRA_OUT_PADRAO",
                severity=Severity(ctx.detector.severity.value),
                score=float(z),
                expected_range={
                    "peer_mean": mu,
                    "peer_stdev": sd,
                    "isolation_threshold": threshold,
                    "n_peers": len(peers),
                    "method": "zscore_per_segment",
                },
                actual_value={
                    "categoria": categoria,
                    "empreiteira": p["empreiteira"],
                    "avg_valor_unitario": p["avg_unit"],
                },
                wf_payment_data_pedido=p["data_ref"],
                supplier_id=p["supplier_id"],
                reason=(
                    f"{p['empreiteira']} ({categoria}): preço médio "
                    f"R$ {p['avg_unit']:.2f} (z={z:+.2f} vs μ {mu:.2f})"
                ),
            )

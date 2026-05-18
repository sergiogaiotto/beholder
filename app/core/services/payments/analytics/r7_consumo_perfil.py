"""R7_CONSUMO_PERFIL — consumo incompatível com perfil regional da empreiteira.

Empreiteira atua em N regiões distintas (regional_soe_nova). Z-score do
volume total contra a distribuição de empreiteiras com o MESMO número de
regiões — peers naturais. Outlier sinaliza volume desproporcional ao
perfil regional.

Proxy do conceito "perfil jurídico" original (DOCX) — não temos esses
dados no schema, então usamos diversificação geográfica como proxy.

Parâmetros:
  zscore_threshold: float — corte (default 2.0)
  min_peers: int          — mínimo de empreiteiras no mesmo bucket (default 3)
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


@register("R7_CONSUMO_PERFIL")
async def r7_consumo_perfil(
    ctx: AnalyticContext,
) -> AsyncIterator[AnalyticFindingDraft]:
    threshold = float(ctx.detector.threshold_params.get("zscore_threshold", 2.0))
    min_peers = int(ctx.detector.threshold_params.get("min_peers", 3))
    universe = universe_filter_for_detector(ctx.detector)
    where_clause = (
        f"WHERE {universe} AND regional_soe_nova IS NOT NULL "
        "AND valor_total_final IS NOT NULL"
        if universe
        else "WHERE regional_soe_nova IS NOT NULL "
        "AND valor_total_final IS NOT NULL"
    )

    async with connect_payments() as c:
        rows = await c.fetch(
            f"""
            SELECT
                wp.empreiteira,
                COUNT(DISTINCT wp.regional_soe_nova) AS n_regionais,
                SUM(wp.valor_total_final)::numeric    AS total_brl,
                MAX(wp.data_pedido)                   AS data_ref,
                sb.id AS supplier_id
            FROM payments.wf_payment wp
            LEFT JOIN payments.supplier_bridge sb ON sb.empreiteira = wp.empreiteira
            {where_clause}
            GROUP BY wp.empreiteira, sb.id
            """
        )

    # Agrupa em buckets de n_regionais.
    by_bucket: dict = defaultdict(list)
    for r in rows:
        by_bucket[int(r["n_regionais"])].append(
            {
                "empreiteira": r["empreiteira"],
                "total": float(r["total_brl"] or 0),
                "data_ref": r["data_ref"],
                "supplier_id": r["supplier_id"],
            }
        )

    for n_reg, peers in by_bucket.items():
        if len(peers) < min_peers:
            continue
        totals = [p["total"] for p in peers]
        sd = stdev(totals)
        if sd == 0.0:
            continue
        mu = mean(totals)
        for p in peers:
            z = zscore(p["total"], totals)
            if abs(z) <= threshold:
                continue
            yield AnalyticFindingDraft(
                detector_code="R7_CONSUMO_PERFIL",
                severity=Severity(ctx.detector.severity.value),
                score=float(z),
                expected_range={
                    "n_regionais": n_reg,
                    "peer_mean_brl": mu,
                    "peer_stdev_brl": sd,
                    "zscore_threshold": threshold,
                    "n_peers": len(peers),
                    "method": "zscore_per_regional_count",
                },
                actual_value={
                    "empreiteira": p["empreiteira"],
                    "total_brl": p["total"],
                    "n_regionais": n_reg,
                },
                wf_payment_data_pedido=p["data_ref"],
                supplier_id=p["supplier_id"],
                reason=(
                    f"{p['empreiteira']} (atua em {n_reg} regional/regionais): "
                    f"R$ {p['total']:.2f} (z={z:+.2f} vs peers μ R$ {mu:.2f})"
                ),
            )

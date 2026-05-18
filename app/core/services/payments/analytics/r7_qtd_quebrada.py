"""R7_QTD_QUEBRADA — quantidades fracionárias atípicas (heurística).

A quantidade implícita do payment é `valor_total_final / valor_unitario`.
Quantidades "redondas" (.00, .50, etc) sugerem medição limpa. Mais de N
casas decimais não-zero é sinal de ajuste manual / cálculo reverso para
encaixar um valor total já decidido — flag de revisão.

Parâmetros:
  decimal_places_max: int — máximo aceitável (default 2)

Heurística pura — sem dependência estatística. Score é a quantidade de
casas decimais não-zero (proxy do "quão quebrada" está).
"""

from __future__ import annotations

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
from app.core.services.payments.analytics._stats import decimal_places


@register("R7_QTD_QUEBRADA")
async def r7_qtd_quebrada(ctx: AnalyticContext) -> AsyncIterator[AnalyticFindingDraft]:
    threshold = int(ctx.detector.threshold_params.get("decimal_places_max", 2))
    universe = universe_filter_for_detector(ctx.detector)
    where_clause = (
        f"WHERE {universe} AND valor_unitario IS NOT NULL "
        "AND valor_unitario != 0 AND valor_total_final IS NOT NULL"
        if universe
        else "WHERE valor_unitario IS NOT NULL "
        "AND valor_unitario != 0 AND valor_total_final IS NOT NULL"
    )

    async with connect_payments() as c:
        rows = await c.fetch(
            f"""
            SELECT
                wp.id, wp.data_pedido, wp.empreiteira, wp.os_num,
                wp.material_servico_num,
                wp.valor_total_final, wp.valor_unitario,
                sb.id AS supplier_id
            FROM payments.wf_payment wp
            LEFT JOIN payments.supplier_bridge sb ON sb.empreiteira = wp.empreiteira
            {where_clause}
            """
        )

    for r in rows:
        valor_total = Decimal(r["valor_total_final"])
        valor_unit = Decimal(r["valor_unitario"])
        if valor_unit == 0:
            continue
        qtd = float(valor_total / valor_unit)
        # round pra evitar ruído de float: 1.0 vira "1.0" e tem 0 casas.
        qtd_rounded = round(qtd, 10)
        dp = decimal_places(qtd_rounded)
        if dp <= threshold:
            continue
        yield AnalyticFindingDraft(
            detector_code="R7_QTD_QUEBRADA",
            severity=Severity(ctx.detector.severity.value),
            score=float(dp),
            expected_range={
                "decimal_places_max": threshold,
                "method": "heuristic",
            },
            actual_value={
                "qtd_implicita": qtd,
                "decimal_places": dp,
                "valor_total_final": float(valor_total),
                "valor_unitario": float(valor_unit),
                "material_servico_num": r["material_servico_num"],
                "empreiteira": r["empreiteira"],
                "os_num": r["os_num"],
            },
            wf_payment_id=int(r["id"]),
            wf_payment_data_pedido=r["data_pedido"],
            supplier_id=r["supplier_id"],
            reason=f"qtd={qtd} tem {dp} casas decimais (> {threshold})",
        )

"""REGRA 6.5 — WF.valor_total_final × EKPO.valor_liquido (math_tolerance, high).

tolerance_pct default 0.5%.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID

from app.adapters.db.postgres_payments import connect_payments
from app.core.domain.payments import Severity
from app.core.services.payments.rules import (
    FindingDraft,
    ReconciliationContext,
    register,
)
from app.core.services.payments.rules._math import within_tolerance_pct


@register("REGRA_6_5")
async def regra_6_5_valor(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    tolerance_pct = Decimal(str(ctx.rule.threshold_params.get("tolerance_pct", 0.5)))
    universe = ctx.universe_filter or "TRUE"
    sql = f"""
        SELECT
            wf.id, wf.data_pedido, wf.pedido_num,
            wf.item_num,
            wf.valor_total_final  AS wf_valor,
            ekpo.valor_liquido    AS ekpo_valor,
            sb.id::text           AS supplier_id,
            cm.id::text           AS contract_master_id
        FROM payments.wf_payment wf
        JOIN payments.supplier_bridge sb
          ON LOWER(sb.empreiteira) = LOWER(wf.empreiteira)
        JOIN payments.contract_master cm
          ON cm.supplier_bridge_id = sb.id AND cm.is_monitored = TRUE
        JOIN payments.purchase_order_item ekpo
          ON ekpo.documento_compras = wf.pedido_num
         AND ekpo.item = wf.item_num
        WHERE ({universe})
          AND wf.valor_total_final IS NOT NULL
          AND ekpo.valor_liquido IS NOT NULL
    """
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        within, delta_pct = within_tolerance_pct(
            r["ekpo_valor"], r["wf_valor"], tolerance_pct
        )
        if within:
            continue
        yield FindingDraft(
            rule_code="REGRA_6_5",
            severity=Severity.HIGH,
            purchase_order_documento=r["pedido_num"] or "",
            purchase_order_item=r["item_num"],
            wf_payment_id=r["id"],
            wf_payment_data_pedido=r["data_pedido"],
            supplier_id=UUID(r["supplier_id"]),
            contract_master_id=UUID(r["contract_master_id"]),
            expected_value={"valor_liquido_ekpo": float(r["ekpo_valor"])},
            actual_value={"valor_total_final_wf": float(r["wf_valor"])},
            delta_pct=delta_pct,
            value_at_risk_brl=abs(r["wf_valor"] - r["ekpo_valor"]),
            reason="valor_fora_tolerancia_ekpo",
        )

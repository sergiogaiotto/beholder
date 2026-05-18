"""REGRA 6.7 — WF.item_num × GC.item (SDD §9, medium).

Join por (contrato_num, item). Finding se GC item ausente.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from app.adapters.db.postgres_payments import connect_payments
from app.core.domain.payments import Severity
from app.core.services.payments.rules import (
    FindingDraft,
    ReconciliationContext,
    register,
)


@register("REGRA_6_7")
async def regra_6_7_gc_item(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    universe = ctx.universe_filter or "TRUE"
    sql = f"""
        SELECT
            wf.id, wf.data_pedido, wf.pedido_num, wf.contrato_num,
            wf.item_num           AS wf_item,
            sb.id::text           AS supplier_id,
            cm.id::text           AS contract_master_id
        FROM payments.wf_payment wf
        JOIN payments.supplier_bridge sb
          ON LOWER(sb.empreiteira) = LOWER(wf.empreiteira)
        JOIN payments.contract_master cm
          ON cm.supplier_bridge_id = sb.id AND cm.is_monitored = TRUE
        LEFT JOIN payments.purchase_order_gc gc
          ON gc.documento_compras = wf.contrato_num
         AND gc.item = wf.item_num
        WHERE ({universe})
          AND wf.contrato_num IS NOT NULL
          AND wf.item_num IS NOT NULL AND wf.item_num <> ''
          AND gc.id IS NULL
    """
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        yield FindingDraft(
            rule_code="REGRA_6_7",
            severity=Severity.MEDIUM,
            purchase_order_documento=r["contrato_num"] or "",
            purchase_order_item=r["wf_item"],
            wf_payment_id=r["id"],
            wf_payment_data_pedido=r["data_pedido"],
            supplier_id=UUID(r["supplier_id"]),
            contract_master_id=UUID(r["contract_master_id"]),
            expected_value={"item_em_gc": r["wf_item"]},
            actual_value={"item_em_gc": None},
            reason="item_inexistente_em_gc",
        )

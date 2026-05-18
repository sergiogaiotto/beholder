"""REGRA 6.4 — WF.item_num × EKPO.item (SDD §9, medium).

Join por (documento_compras, item). Finding se EKPO Item ausente.
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


@register("REGRA_6_4")
async def regra_6_4_item(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    universe = ctx.universe_filter or "TRUE"
    sql = f"""
        SELECT
            wf.id, wf.data_pedido, wf.pedido_num,
            wf.item_num           AS wf_item,
            sb.id::text           AS supplier_id,
            cm.id::text           AS contract_master_id
        FROM payments.wf_payment wf
        JOIN payments.supplier_bridge sb
          ON LOWER(sb.empreiteira) = LOWER(wf.empreiteira)
        JOIN payments.contract_master cm
          ON cm.supplier_bridge_id = sb.id AND cm.is_monitored = TRUE
        LEFT JOIN payments.purchase_order_item ekpo
          ON ekpo.documento_compras = wf.pedido_num
         AND ekpo.item = wf.item_num
        WHERE ({universe})
          AND wf.pedido_num IS NOT NULL AND wf.pedido_num <> ''
          AND wf.item_num IS NOT NULL AND wf.item_num <> ''
          AND ekpo.id IS NULL
    """
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        yield FindingDraft(
            rule_code="REGRA_6_4",
            severity=Severity.MEDIUM,
            purchase_order_documento=r["pedido_num"] or "",
            purchase_order_item=r["wf_item"],
            wf_payment_id=r["id"],
            wf_payment_data_pedido=r["data_pedido"],
            supplier_id=UUID(r["supplier_id"]),
            contract_master_id=UUID(r["contract_master_id"]),
            expected_value={"item_em_ekpo": r["wf_item"]},
            actual_value={"item_em_ekpo": None},
            reason="item_inexistente_em_ekpo",
        )

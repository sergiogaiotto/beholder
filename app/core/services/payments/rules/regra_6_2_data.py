"""REGRA 6.2 — WF.data_pedido × EKKO.data_documento (SDD §9, medium).

Tolerância em dias (default 7).
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


@register("REGRA_6_2")
async def regra_6_2_data(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    tolerance_days = int(ctx.rule.threshold_params.get("date_tolerance_days", 7))
    universe = ctx.universe_filter or "TRUE"
    sql = f"""
        SELECT
            wf.id, wf.data_pedido AS wf_data,
            wf.pedido_num,
            poh.data_documento    AS poh_data,
            sb.id::text           AS supplier_id,
            cm.id::text           AS contract_master_id,
            ABS(wf.data_pedido - poh.data_documento) AS delta_days
        FROM payments.wf_payment wf
        JOIN payments.supplier_bridge sb
          ON LOWER(sb.empreiteira) = LOWER(wf.empreiteira)
        JOIN payments.contract_master cm
          ON cm.supplier_bridge_id = sb.id AND cm.is_monitored = TRUE
        JOIN payments.purchase_order_header poh
          ON poh.documento_compras = wf.pedido_num
        WHERE ({universe})
          AND wf.pedido_num IS NOT NULL AND wf.pedido_num <> ''
          AND poh.data_documento IS NOT NULL
          AND ABS(wf.data_pedido - poh.data_documento) > $1
    """
    async with connect_payments() as conn:
        rows = await conn.fetch(sql, tolerance_days)

    for r in rows:
        yield FindingDraft(
            rule_code="REGRA_6_2",
            severity=Severity.MEDIUM,
            purchase_order_documento=r["pedido_num"] or "",
            wf_payment_id=r["id"],
            wf_payment_data_pedido=r["wf_data"],
            supplier_id=UUID(r["supplier_id"]),
            contract_master_id=UUID(r["contract_master_id"]),
            expected_value={"data_ekko": r["poh_data"].isoformat()},
            actual_value={"data_wf": r["wf_data"].isoformat(), "delta_dias": int(r["delta_days"])},
            reason="data_pedido_fora_tolerancia",
        )

"""REGRA 6.3 — WF.contrato_num × EKKO.contrato_basico (SDD §9, high)."""

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


@register("REGRA_6_3")
async def regra_6_3_contrato(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    universe = ctx.universe_filter or "TRUE"
    sql = f"""
        SELECT
            wf.id, wf.data_pedido, wf.pedido_num,
            wf.contrato_num       AS wf_contrato,
            poh.contrato_basico   AS poh_contrato,
            sb.id::text           AS supplier_id,
            cm.id::text           AS contract_master_id
        FROM payments.wf_payment wf
        JOIN payments.supplier_bridge sb
          ON LOWER(sb.empreiteira) = LOWER(wf.empreiteira)
        JOIN payments.contract_master cm
          ON cm.supplier_bridge_id = sb.id AND cm.is_monitored = TRUE
        JOIN payments.purchase_order_header poh
          ON poh.documento_compras = wf.pedido_num
        WHERE ({universe})
          AND wf.pedido_num IS NOT NULL AND wf.pedido_num <> ''
          AND wf.contrato_num IS NOT NULL
          AND wf.contrato_num <> COALESCE(poh.contrato_basico, '')
    """
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        yield FindingDraft(
            rule_code="REGRA_6_3",
            severity=Severity.HIGH,
            purchase_order_documento=r["pedido_num"] or "",
            wf_payment_id=r["id"],
            wf_payment_data_pedido=r["data_pedido"],
            supplier_id=UUID(r["supplier_id"]),
            contract_master_id=UUID(r["contract_master_id"]),
            expected_value={"contrato_basico_ekko": r["poh_contrato"]},
            actual_value={"contrato_num_wf": r["wf_contrato"]},
            reason="contrato_num_diverge_ekko",
        )

"""REGRA 6.1 — WF.pedido_num × EKPO/Header.documento_compras (SDD §9, high).

LEFT JOIN: finding se EKPO faltando (pedido WF não existe no SAP).
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


@register("REGRA_6_1")
async def regra_6_1_pedido(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    universe = ctx.universe_filter or "TRUE"
    sql = f"""
        SELECT
            wf.id              AS wf_id,
            wf.data_pedido     AS wf_data_pedido,
            wf.pedido_num      AS wf_pedido_num,
            wf.contrato_num    AS wf_contrato_num,
            sb.id::text        AS supplier_id,
            cm.id::text        AS contract_master_id
        FROM payments.wf_payment wf
        JOIN payments.supplier_bridge sb
          ON LOWER(sb.empreiteira) = LOWER(wf.empreiteira)
        JOIN payments.contract_master cm
          ON cm.supplier_bridge_id = sb.id
         AND cm.is_monitored = TRUE
        LEFT JOIN payments.purchase_order_header poh
          ON poh.documento_compras = wf.pedido_num
        WHERE ({universe})
          AND wf.pedido_num IS NOT NULL AND wf.pedido_num <> ''
          AND poh.id IS NULL
    """
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        yield FindingDraft(
            rule_code="REGRA_6_1",
            severity=Severity.HIGH,
            purchase_order_documento=r["wf_pedido_num"] or "",
            wf_payment_id=r["wf_id"],
            wf_payment_data_pedido=r["wf_data_pedido"],
            supplier_id=UUID(r["supplier_id"]),
            contract_master_id=UUID(r["contract_master_id"]),
            expected_value={"pedido_num_em_ekpo": r["wf_pedido_num"]},
            actual_value={"pedido_num_em_ekpo": None},
            reason="pedido_inexistente_em_ekpo",
        )

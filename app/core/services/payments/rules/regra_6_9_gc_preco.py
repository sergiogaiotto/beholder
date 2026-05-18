"""REGRA 6.9 — WF.valor_unitario × GC.preco_bruto_lpu (math_tolerance, high).

tolerance_pct default 1.0% (alinhado com REGRA_LPU).
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


@register("REGRA_6_9")
async def regra_6_9_gc_preco(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    tolerance_pct = Decimal(str(ctx.rule.threshold_params.get("tolerance_pct", 1.0)))
    universe = ctx.universe_filter or "TRUE"
    sql = f"""
        SELECT
            wf.id, wf.data_pedido, wf.pedido_num, wf.contrato_num, wf.item_num,
            wf.valor_unitario     AS wf_preco,
            gc.preco_bruto_lpu    AS gc_preco,
            sb.id::text           AS supplier_id,
            cm.id::text           AS contract_master_id
        FROM payments.wf_payment wf
        JOIN payments.supplier_bridge sb
          ON LOWER(sb.empreiteira) = LOWER(wf.empreiteira)
        JOIN payments.contract_master cm
          ON cm.supplier_bridge_id = sb.id AND cm.is_monitored = TRUE
        JOIN payments.purchase_order_gc gc
          ON gc.documento_compras = wf.contrato_num
         AND gc.item = wf.item_num
        WHERE ({universe})
          AND wf.valor_unitario IS NOT NULL
          AND gc.preco_bruto_lpu IS NOT NULL
    """
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        within, delta_pct = within_tolerance_pct(
            r["gc_preco"], r["wf_preco"], tolerance_pct
        )
        if within:
            continue
        yield FindingDraft(
            rule_code="REGRA_6_9",
            severity=Severity.HIGH,
            purchase_order_documento=r["contrato_num"] or "",
            purchase_order_item=r["item_num"],
            wf_payment_id=r["id"],
            wf_payment_data_pedido=r["data_pedido"],
            supplier_id=UUID(r["supplier_id"]),
            contract_master_id=UUID(r["contract_master_id"]),
            expected_value={"preco_bruto_lpu_gc": float(r["gc_preco"])},
            actual_value={"valor_unitario_wf": float(r["wf_preco"])},
            delta_pct=delta_pct,
            value_at_risk_brl=abs(r["wf_preco"] - r["gc_preco"]),
            reason="preco_unitario_fora_tolerancia_gc",
        )

"""REGRA 6.8 — WF.item_descricao × GC.texto_breve (fuzzy 0.85, medium)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from app.adapters.db.postgres_payments import connect_payments
from app.core.domain.payments import Severity
from app.core.services.payments.rules import (
    FindingDraft,
    ReconciliationContext,
    fuzzy_match,
    register,
)


@register("REGRA_6_8")
async def regra_6_8_gc_descricao(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    threshold = float(ctx.rule.threshold_params.get("fuzzy_threshold", 0.85))
    universe = ctx.universe_filter or "TRUE"
    sql = f"""
        SELECT
            wf.id, wf.data_pedido, wf.pedido_num, wf.contrato_num,
            wf.item_num,
            wf.item_descricao     AS wf_desc,
            gc.texto_breve        AS gc_desc,
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
          AND wf.item_descricao IS NOT NULL AND wf.item_descricao <> ''
    """
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        matched, score = fuzzy_match(r["wf_desc"], r["gc_desc"], threshold=threshold)
        if matched:
            continue
        yield FindingDraft(
            rule_code="REGRA_6_8",
            severity=Severity.MEDIUM,
            purchase_order_documento=r["contrato_num"] or "",
            purchase_order_item=r["item_num"],
            wf_payment_id=r["id"],
            wf_payment_data_pedido=r["data_pedido"],
            supplier_id=UUID(r["supplier_id"]),
            contract_master_id=UUID(r["contract_master_id"]),
            expected_value={"descricao_gc": r["gc_desc"]},
            actual_value={"descricao_wf": r["wf_desc"], "fuzzy_score": score},
            reason="descricao_fuzzy_baixa",
        )

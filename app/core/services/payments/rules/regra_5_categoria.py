"""REGRA 5.e — Categoria do pagamento × categoria do supplier_bridge (fuzzy 0.90).

Diferente das outras R5: aqui compara wf.categoria × supplier_bridge.categoria
(NÃO contract_version). Cardinalidade 11 (Pré-B §3.3) — fuzzy mesmo assim
por causa de variações ortográficas ("ATIVAÇÃO" vs "ATIVACAO").
"""

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
from app.core.services.payments.rules._wf_cv_join import wf_vigente_join_sql


@register("REGRA_5_CATEGORIA")
async def regra_5_categoria(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    threshold = float(ctx.rule.threshold_params.get("fuzzy_threshold", 0.90))

    # Pega categoria do supplier_bridge (sb.categoria) via join base
    sql = wf_vigente_join_sql(
        wf_fields="wf.categoria AS wf_cat",
        extra_where="wf.categoria IS NOT NULL AND wf.categoria <> ''",
        universe_filter=ctx.universe_filter or "TRUE",
    )
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        matched, score = fuzzy_match(
            r["wf_cat"], r["supplier_categoria"], threshold=threshold
        )
        if not matched:
            yield FindingDraft(
                rule_code="REGRA_5_CATEGORIA",
                severity=Severity.MEDIUM,
                purchase_order_documento=r["wf_pedido_num"] or r["wf_contrato_num"] or "",
                wf_payment_id=r["wf_id"],
                wf_payment_data_pedido=r["wf_data_pedido"],
                supplier_id=UUID(r["supplier_id"]),
                contract_master_id=UUID(r["contract_master_id"]),
                contract_version_id=UUID(r["contract_version_id"]),
                expected_value={"categoria_supplier": r["supplier_categoria"]},
                actual_value={"categoria_pagamento": r["wf_cat"], "fuzzy_score": score},
                reason="categoria_fuzzy_baixa",
            )

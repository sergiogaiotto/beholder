"""REGRA 5.f — Objeto do contrato (fuzzy 0.85, SDD §9 v1.1.1, medium).

Pré-B descobriu: OBJETO_DO_CONTRATO tem 598 valores únicos no WF — é
taxonomia controlada, NÃO texto livre. Logo cascata fuzzy→embedding→LLM
proposta na v1.1 está over-engineered. R5.f vira fuzzy puro com threshold
0.85 (mais permissivo que 5.c/d/e porque cardinalidade é maior).

Zero chamadas LLM — economia estimada R$10-50k/mês.
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


@register("REGRA_5_OBJETO")
async def regra_5_objeto(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    threshold = float(ctx.rule.threshold_params.get("fuzzy_threshold", 0.85))

    sql = wf_vigente_join_sql(
        wf_fields="wf.objeto_do_contrato AS wf_objeto",
        cv_fields="cv.objeto_contrato AS cv_objeto",
        extra_where="wf.objeto_do_contrato IS NOT NULL AND wf.objeto_do_contrato <> ''",
        universe_filter=ctx.universe_filter or "TRUE",
    )
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        matched, score = fuzzy_match(r["wf_objeto"], r["cv_objeto"], threshold=threshold)
        if not matched:
            yield FindingDraft(
                rule_code="REGRA_5_OBJETO",
                severity=Severity.MEDIUM,
                purchase_order_documento=r["wf_pedido_num"] or r["wf_contrato_num"] or "",
                wf_payment_id=r["wf_id"],
                wf_payment_data_pedido=r["wf_data_pedido"],
                supplier_id=UUID(r["supplier_id"]),
                contract_master_id=UUID(r["contract_master_id"]),
                contract_version_id=UUID(r["contract_version_id"]),
                expected_value={"objeto_contrato": r["cv_objeto"]},
                actual_value={"objeto_pagamento": r["wf_objeto"], "fuzzy_score": score},
                reason="objeto_fuzzy_baixa",
            )

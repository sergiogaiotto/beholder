"""REGRA 5.d — Atividade (fuzzy 0.90, SDD §9, medium)."""

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


@register("REGRA_5_ATIVIDADE")
async def regra_5_atividade(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    threshold = float(ctx.rule.threshold_params.get("fuzzy_threshold", 0.90))

    sql = wf_vigente_join_sql(
        wf_fields="wf.atividade AS wf_ativ",
        cv_fields="cv.atividade AS cv_ativ",
        extra_where="wf.atividade IS NOT NULL AND wf.atividade <> ''",
        universe_filter=ctx.universe_filter or "TRUE",
    )
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        matched, score = fuzzy_match(r["wf_ativ"], r["cv_ativ"], threshold=threshold)
        if not matched:
            yield FindingDraft(
                rule_code="REGRA_5_ATIVIDADE",
                severity=Severity.MEDIUM,
                purchase_order_documento=r["wf_pedido_num"] or r["wf_contrato_num"] or "",
                wf_payment_id=r["wf_id"],
                wf_payment_data_pedido=r["wf_data_pedido"],
                supplier_id=UUID(r["supplier_id"]),
                contract_master_id=UUID(r["contract_master_id"]),
                contract_version_id=UUID(r["contract_version_id"]),
                expected_value={"atividade_contrato": r["cv_ativ"]},
                actual_value={"atividade_pagamento": r["wf_ativ"], "fuzzy_score": score},
                reason="atividade_fuzzy_baixa",
            )

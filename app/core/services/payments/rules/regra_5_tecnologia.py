"""REGRA 5.c — Tecnologia (fuzzy 0.90, SDD §9, medium)."""

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


@register("REGRA_5_TECNOLOGIA")
async def regra_5_tecnologia(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    threshold = float(ctx.rule.threshold_params.get("fuzzy_threshold", 0.90))

    sql = wf_vigente_join_sql(
        wf_fields="wf.tecnologia AS wf_tec",
        cv_fields="cv.tecnologia AS cv_tec",
        extra_where="wf.tecnologia IS NOT NULL AND wf.tecnologia <> ''",
        universe_filter=ctx.universe_filter or "TRUE",
    )
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        matched, score = fuzzy_match(r["wf_tec"], r["cv_tec"], threshold=threshold)
        if not matched:
            yield FindingDraft(
                rule_code="REGRA_5_TECNOLOGIA",
                severity=Severity.MEDIUM,
                purchase_order_documento=r["wf_pedido_num"] or r["wf_contrato_num"] or "",
                wf_payment_id=r["wf_id"],
                wf_payment_data_pedido=r["wf_data_pedido"],
                supplier_id=UUID(r["supplier_id"]),
                contract_master_id=UUID(r["contract_master_id"]),
                contract_version_id=UUID(r["contract_version_id"]),
                expected_value={"tecnologia_contrato": r["cv_tec"]},
                actual_value={"tecnologia_pagamento": r["wf_tec"], "fuzzy_score": score},
                reason="tecnologia_fuzzy_baixa",
            )

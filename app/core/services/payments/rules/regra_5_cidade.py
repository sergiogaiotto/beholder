"""REGRA 5.b — Cidade do pagamento × cidades cobertas pelo contrato (SDD §9, medium).

normalize(wf.cidade) ∈ {normalize(c) for c in cv.cidade}. Normalização
em Python (não SQL) para garantir mesma semântica do helper _normalize.
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
from app.core.services.payments.rules._normalize import normalize_text
from app.core.services.payments.rules._wf_cv_join import wf_vigente_join_sql


@register("REGRA_5_CIDADE")
async def regra_5_cidade(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    """wf.cidade NOT IN cv.cidade[] (após normalize) → finding."""
    sql = wf_vigente_join_sql(
        wf_fields="wf.cidade AS wf_cidade",
        cv_fields="cv.cidade AS cv_cidade",
        extra_where="wf.cidade IS NOT NULL AND wf.cidade <> ''",
        universe_filter=ctx.universe_filter or "TRUE",
    )

    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        wf_norm = normalize_text(r["wf_cidade"])
        cv_set = {normalize_text(c) for c in (r["cv_cidade"] or [])}
        if not cv_set or wf_norm not in cv_set:
            yield FindingDraft(
                rule_code="REGRA_5_CIDADE",
                severity=Severity.MEDIUM,
                purchase_order_documento=r["wf_pedido_num"] or r["wf_contrato_num"] or "",
                wf_payment_id=r["wf_id"],
                wf_payment_data_pedido=r["wf_data_pedido"],
                supplier_id=UUID(r["supplier_id"]),
                contract_master_id=UUID(r["contract_master_id"]),
                contract_version_id=UUID(r["contract_version_id"]),
                expected_value={"cidades_contrato": list(r["cv_cidade"] or [])},
                actual_value={"cidade_pagamento": r["wf_cidade"]},
                reason="cidade_fora_contrato",
            )

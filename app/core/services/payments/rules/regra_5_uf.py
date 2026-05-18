"""REGRA 5.a — UF do pagamento × UFs cobertas pelo contrato (SDD §9, medium).

WF.uf ∈ contract_version.uf[] (após uppercase). SQL puro.
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
from app.core.services.payments.rules._wf_cv_join import wf_vigente_join_sql


@register("REGRA_5_UF")
async def regra_5_uf(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    """wf.uf NOT IN cv.uf[] → finding."""
    sql = wf_vigente_join_sql(
        wf_fields="wf.uf AS wf_uf",
        cv_fields="cv.uf AS cv_uf",
        extra_where="""
            wf.uf IS NOT NULL
            AND (
                cv.uf IS NULL
                OR cardinality(cv.uf) = 0
                OR NOT (UPPER(wf.uf) = ANY(SELECT UPPER(unnest(cv.uf))))
            )
        """,
        universe_filter=ctx.universe_filter or "TRUE",
    )

    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        yield FindingDraft(
            rule_code="REGRA_5_UF",
            severity=Severity.MEDIUM,
            purchase_order_documento=r["wf_pedido_num"] or r["wf_contrato_num"] or "",
            wf_payment_id=r["wf_id"],
            wf_payment_data_pedido=r["wf_data_pedido"],
            supplier_id=UUID(r["supplier_id"]),
            contract_master_id=UUID(r["contract_master_id"]),
            contract_version_id=UUID(r["contract_version_id"]),
            expected_value={"uf_contrato": list(r["cv_uf"] or [])},
            actual_value={"uf_pagamento": r["wf_uf"]},
            reason="uf_fora_contrato",
        )

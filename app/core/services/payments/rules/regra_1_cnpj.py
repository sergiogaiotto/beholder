"""REGRA 1 — CNPJ match base ↔ PDF (SDD §9, severity=high).

Compara `supplier_bridge.cnpj` (vindo da DE-PARA "Contratos-Empreteiras")
com `contract_master.cnpj` (vindo do PDF extraído). Mismatch = finding.

Escopo: apenas contratos monitorados (`is_monitored=TRUE`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.adapters.db.postgres_payments import connect_payments
from app.core.domain.payments import Severity
from app.core.services.payments.rules import (
    FindingDraft,
    ReconciliationContext,
    register,
)


@register("REGRA_1")
async def regra_1_cnpj(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    """SELECT contract_master.cnpj != supplier_bridge.cnpj."""
    sql = """
        SELECT cm.id::text         AS contract_master_id,
               cm.contrato_num_sap AS documento_compras,
               cm.cnpj             AS pdf_cnpj,
               sb.id::text         AS supplier_id,
               sb.cnpj             AS base_cnpj
        FROM payments.contract_master cm
        JOIN payments.supplier_bridge sb ON sb.id = cm.supplier_bridge_id
        WHERE cm.cnpj <> sb.cnpj
          AND cm.is_monitored = TRUE
    """
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    from uuid import UUID
    for r in rows:
        yield FindingDraft(
            rule_code="REGRA_1",
            severity=Severity.HIGH,
            purchase_order_documento=r["documento_compras"],
            contract_master_id=UUID(r["contract_master_id"]),
            supplier_id=UUID(r["supplier_id"]),
            expected_value={"cnpj": r["base_cnpj"]},
            actual_value={"cnpj": r["pdf_cnpj"]},
            reason="cnpj_mismatch_base_pdf",
        )

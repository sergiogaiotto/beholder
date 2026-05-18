"""REGRA 3 — Texto Breve + Preço LPU base ↔ PDF (SDD §9, severity=medium).

DOCX original: "Bater Base 'Contratos – Empreteiras' campos 'Texto Breve'
e 'Preço bruto (LPU)' com o PDF."

Cruza purchase_order_gc (sheet Contratos Guarda Chuvas) × lpu_item extraído
do PDF (source='pdf'). Texto breve divergente OU preço fora tolerância.

Threshold: tolerance_pct (default 1.0).
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


@register("REGRA_3")
async def regra_3_texto_preco(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    """GC × LPU(pdf): texto_breve divergente OU preço fora de tolerância."""
    tolerance_pct = Decimal(str(
        ctx.rule.threshold_params.get("tolerance_pct", 1.0)
    ))

    sql = """
        SELECT
            gc.documento_compras,
            gc.item                       AS item_num,
            gc.numero_servico,
            gc.texto_breve                AS base_texto,
            gc.preco_bruto_lpu            AS base_preco,
            lpu.descricao                 AS pdf_texto,
            lpu.preco_unitario            AS pdf_preco,
            cm.id::text                   AS contract_master_id,
            cv.id::text                   AS contract_version_id
        FROM payments.purchase_order_gc gc
        JOIN payments.contract_master cm
          ON cm.contrato_num_sap = gc.documento_compras
         AND cm.is_monitored = TRUE
        JOIN payments.contract_version cv
          ON cv.id = cm.current_version_id
        JOIN payments.lpu_item lpu
          ON lpu.contract_version_id = cv.id
         AND lpu.numero_servico = gc.numero_servico
         AND lpu.source = 'pdf'
    """

    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        cmid = UUID(r["contract_master_id"])
        cvid = UUID(r["contract_version_id"])
        base_texto = r["base_texto"] or ""
        pdf_texto = r["pdf_texto"] or ""
        base_preco = r["base_preco"]
        pdf_preco = r["pdf_preco"]

        # Texto divergente (case-sensitive — match exato; tolerância R3 é só preço)
        texto_diverge = base_texto != pdf_texto

        # Preço fora de tolerância
        preco_diverge = False
        delta_pct = None
        if base_preco is not None and pdf_preco is not None and pdf_preco > 0:
            delta = abs(base_preco - pdf_preco)
            delta_pct = float(delta / pdf_preco * 100)
            preco_diverge = delta_pct > float(tolerance_pct)

        if texto_diverge or preco_diverge:
            yield FindingDraft(
                rule_code="REGRA_3",
                severity=Severity.MEDIUM,
                purchase_order_documento=r["documento_compras"],
                purchase_order_item=r["item_num"],
                contract_master_id=cmid,
                contract_version_id=cvid,
                expected_value={
                    "texto_breve_pdf": pdf_texto,
                    "preco_pdf": float(pdf_preco) if pdf_preco is not None else None,
                },
                actual_value={
                    "texto_breve_base": base_texto,
                    "preco_base": float(base_preco) if base_preco is not None else None,
                },
                delta_pct=delta_pct,
                reason=(
                    "texto_e_preco_divergem" if (texto_diverge and preco_diverge)
                    else "texto_diverge" if texto_diverge
                    else "preco_diverge"
                ),
            )

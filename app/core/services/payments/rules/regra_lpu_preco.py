"""REGRA_LPU — Preço aplicado ↔ LPU vigente do PDF (SDD §9, high).

Para cada ESLL (service_package):
  1. Resolve ContractVersion vigente via EKPO → EKKO → data_documento
  2. Procura LPUItem do serviço (numero_servico) naquela CV
  3. Se LPU ausente: finding 'servico_fora_da_lpu'
  4. Se LPU presente: compara ESLL.preco_bruto × LPU.preco_unitario com
     tolerance_pct (default 1.0%).

value_at_risk_brl = |delta_preco| * qtd_solicitada (impacto monetário).
evidence_pages = [LPU.pagina_pdf] quando disponível.

Não usa universe_filter — REGRA_LPU não cruza com wf_payment.
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


@register("REGRA_LPU")
async def regra_lpu_preco(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    tolerance_pct = Decimal(str(ctx.rule.threshold_params.get("tolerance_pct", 1.0)))
    sql = """
        SELECT
            esll.id              AS esll_id,
            esll.pacote          AS esll_pacote,
            esll.linha           AS esll_linha,
            esll.numero_servico  AS esll_servico,
            esll.preco_bruto     AS esll_preco,
            esll.qtd_solicitada  AS esll_qtd,
            esll.ekpo_documento  AS ekpo_doc,
            esll.ekpo_item       AS ekpo_item,

            poh.documento_compras AS poh_doc,
            poh.data_documento    AS poh_data,

            sb.id::text  AS supplier_id,
            cm.id::text  AS contract_master_id,
            cv.id::text  AS contract_version_id,

            lpu.id               AS lpu_id,
            lpu.preco_unitario   AS lpu_preco,
            lpu.pagina_pdf       AS lpu_pagina
        FROM payments.service_package esll
        JOIN payments.purchase_order_item ekpo
          ON ekpo.documento_compras = esll.ekpo_documento
         AND ekpo.item = esll.ekpo_item
        JOIN payments.purchase_order_header poh
          ON poh.documento_compras = ekpo.documento_compras
        JOIN payments.supplier_bridge sb
          ON sb.numero_fornecedor_sap = poh.fornecedor
        JOIN payments.contract_master cm
          ON cm.supplier_bridge_id = sb.id
         AND cm.is_monitored = TRUE
        LEFT JOIN payments.contract_version cv
          ON cv.contract_master_id = cm.id
         AND poh.data_documento BETWEEN cv.valid_from AND cv.valid_to
        LEFT JOIN payments.lpu_item lpu
          ON lpu.contract_version_id = cv.id
         AND lpu.numero_servico = esll.numero_servico
        WHERE esll.preco_bruto IS NOT NULL
          AND poh.data_documento IS NOT NULL
    """
    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        # Sem CV vigente — pula (R2 deve pegar essa); REGRA_LPU foca em preço
        cvid_str = r["contract_version_id"]
        if cvid_str is None:
            continue

        supplier_id = UUID(r["supplier_id"])
        cm_id = UUID(r["contract_master_id"])
        cv_id = UUID(cvid_str)

        if r["lpu_id"] is None:
            yield FindingDraft(
                rule_code="REGRA_LPU",
                severity=Severity.HIGH,
                purchase_order_documento=r["poh_doc"] or "",
                supplier_id=supplier_id,
                contract_master_id=cm_id,
                contract_version_id=cv_id,
                expected_value={
                    "lpu_item_para_servico": r["esll_servico"],
                },
                actual_value={
                    "lpu_item_encontrado": None,
                    "esll_preco_bruto": float(r["esll_preco"]),
                },
                reason="servico_fora_da_lpu",
            )
            continue

        within, delta_pct = within_tolerance_pct(
            r["lpu_preco"], r["esll_preco"], tolerance_pct
        )
        if within:
            continue

        var_brl: Decimal = abs(r["esll_preco"] - r["lpu_preco"])
        if r["esll_qtd"] is not None:
            var_brl = var_brl * r["esll_qtd"]

        yield FindingDraft(
            rule_code="REGRA_LPU",
            severity=Severity.HIGH,
            purchase_order_documento=r["poh_doc"] or "",
            supplier_id=supplier_id,
            contract_master_id=cm_id,
            contract_version_id=cv_id,
            expected_value={
                "preco_unitario_lpu": float(r["lpu_preco"]),
                "numero_servico": r["esll_servico"],
            },
            actual_value={
                "preco_bruto_esll": float(r["esll_preco"]),
                "qtd_solicitada": float(r["esll_qtd"]) if r["esll_qtd"] is not None else None,
            },
            delta_pct=delta_pct,
            value_at_risk_brl=var_brl,
            evidence_pages=[int(r["lpu_pagina"])] if r["lpu_pagina"] else [],
            reason="preco_lpu_fora_tolerancia",
        )

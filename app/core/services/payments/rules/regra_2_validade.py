"""REGRA 2 — Validade + ValFix base ↔ PDF (SDD §9, severity=high).

Para cada PurchaseOrderHeader (EKKO), checa se existe ContractVersion
vigente em `data_documento`. Vigência = `valid_from ≤ data ≤ valid_to`
com tolerância opcional `date_tolerance_days` (default 0).

Também valida `val_fix_cab` entre EKKO e contract_version (se ambos
populados). Mismatch de valor = finding adicional no mesmo row.

Escopo: PO de contratos monitorados (via supplier_bridge → contract_master).
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


@register("REGRA_2")
async def regra_2_validade(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    """LEFT JOIN PO × contract_version vigente; emite finding se sem match
    ou val_fix_cab divergente.
    """
    tolerance_days = int(
        ctx.rule.threshold_params.get("date_tolerance_days", 0)
    )

    # PO.data_documento deve estar entre cv.valid_from e cv.valid_to
    # (com tolerância). LEFT JOIN: PO sem versão vigente → finding tipo "sem_versao".
    sql = """
        WITH po_monitored AS (
            SELECT poh.id, poh.documento_compras, poh.fornecedor,
                   poh.data_documento, poh.val_fix_cab,
                   sb.id AS supplier_id, cm.id AS contract_master_id
            FROM payments.purchase_order_header poh
            JOIN payments.supplier_bridge sb
              ON sb.numero_fornecedor_sap = poh.fornecedor
            JOIN payments.contract_master cm
              ON cm.supplier_bridge_id = sb.id
             AND cm.is_monitored = TRUE
            WHERE poh.data_documento IS NOT NULL
        )
        SELECT
            po.documento_compras,
            po.data_documento,
            po.val_fix_cab AS po_val_fix_cab,
            po.supplier_id::text AS supplier_id,
            po.contract_master_id::text AS contract_master_id,
            cv.id::text   AS contract_version_id,
            cv.valid_from AS cv_valid_from,
            cv.valid_to   AS cv_valid_to,
            cv.val_fix_cab AS cv_val_fix_cab
        FROM po_monitored po
        LEFT JOIN payments.contract_version cv
          ON cv.contract_master_id = po.contract_master_id
         AND po.data_documento
             BETWEEN cv.valid_from - ($1::int * INTERVAL '1 day')
                 AND cv.valid_to   + ($1::int * INTERVAL '1 day')
    """

    async with connect_payments() as conn:
        rows = await conn.fetch(sql, tolerance_days)

    for r in rows:
        sid = UUID(r["supplier_id"])
        cmid = UUID(r["contract_master_id"])
        cvid = UUID(r["contract_version_id"]) if r["contract_version_id"] else None

        if cvid is None:
            # PO em data sem ContractVersion vigente
            yield FindingDraft(
                rule_code="REGRA_2",
                severity=Severity.HIGH,
                purchase_order_documento=r["documento_compras"],
                supplier_id=sid,
                contract_master_id=cmid,
                expected_value={
                    "vigencia_em": r["data_documento"].isoformat(),
                    "criterio": f"valid_from - {tolerance_days}d <= data <= valid_to + {tolerance_days}d",
                },
                actual_value={
                    "data_documento": r["data_documento"].isoformat(),
                    "contract_version_vigente": None,
                },
                reason="sem_versao_vigente",
            )
            continue

        # Se ambos val_fix_cab populados, comparar (exato — sem tolerance pra valor)
        po_v = r["po_val_fix_cab"]
        cv_v = r["cv_val_fix_cab"]
        if po_v is not None and cv_v is not None and po_v != cv_v:
            yield FindingDraft(
                rule_code="REGRA_2",
                severity=Severity.HIGH,
                purchase_order_documento=r["documento_compras"],
                supplier_id=sid,
                contract_master_id=cmid,
                contract_version_id=cvid,
                expected_value={"val_fix_cab": float(cv_v)},
                actual_value={"val_fix_cab": float(po_v)},
                reason="val_fix_cab_mismatch",
            )

"""REGRA 4 — Cobertura de extração (SDD §9, severity=medium).

DOCX original (parte 1): "Memorizar escopo, região, valores fixos e
variáveis para cada contrato para usar como base de avaliação."
→ implementado pelo extraction_service na Fase 4 (não gera finding).

Parte 2 (check derivada): alerta quando a extração ficou incompleta —
preserva semântica do SDD v1.0. Conta campos NULL entre os 6 essenciais:
  objeto_contrato, tecnologia, atividade, uf, cidade, val_fix_cab
Threshold implícito: >2 campos NULL → finding.

Escopo: current_version_id dos contract_master monitorados.
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


_MAX_NULL_FIELDS_DEFAULT = 2  # > 2 campos null → finding


@register("REGRA_4")
async def regra_4_cobertura(
    ctx: ReconciliationContext,
) -> AsyncIterator[FindingDraft]:
    """Alerta contratos com cobertura de extração insuficiente."""
    max_null = int(
        ctx.rule.threshold_params.get("max_null_fields", _MAX_NULL_FIELDS_DEFAULT)
    )

    sql = """
        SELECT
            cm.id::text       AS contract_master_id,
            cm.contrato_num_sap AS documento_compras,
            cv.id::text       AS contract_version_id,
            cv.objeto_contrato IS NULL AS missing_objeto,
            cv.tecnologia IS NULL OR cv.tecnologia = '' AS missing_tecnologia,
            cv.atividade IS NULL OR cv.atividade = ''   AS missing_atividade,
            (cv.uf IS NULL OR cardinality(cv.uf) = 0)   AS missing_uf,
            (cv.cidade IS NULL OR cardinality(cv.cidade) = 0) AS missing_cidade,
            cv.val_fix_cab IS NULL AS missing_val_fix_cab
        FROM payments.contract_master cm
        JOIN payments.contract_version cv
          ON cv.id = cm.current_version_id
        WHERE cm.is_monitored = TRUE
    """

    async with connect_payments() as conn:
        rows = await conn.fetch(sql)

    for r in rows:
        missing_fields = [
            ("objeto_contrato", r["missing_objeto"]),
            ("tecnologia",      r["missing_tecnologia"]),
            ("atividade",       r["missing_atividade"]),
            ("uf",              r["missing_uf"]),
            ("cidade",          r["missing_cidade"]),
            ("val_fix_cab",     r["missing_val_fix_cab"]),
        ]
        missing_names = [name for name, is_missing in missing_fields if is_missing]

        if len(missing_names) > max_null:
            yield FindingDraft(
                rule_code="REGRA_4",
                severity=Severity.MEDIUM,
                purchase_order_documento=r["documento_compras"],
                contract_master_id=UUID(r["contract_master_id"]),
                contract_version_id=UUID(r["contract_version_id"]),
                expected_value={
                    "max_null_fields": max_null,
                    "fields_checked": [n for n, _ in missing_fields],
                },
                actual_value={
                    "null_count": len(missing_names),
                    "missing_fields": missing_names,
                },
                reason="cobertura_extracao_insuficiente",
            )

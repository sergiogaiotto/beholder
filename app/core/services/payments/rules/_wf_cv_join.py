"""Helper SQL: WFPayment × ContractVersion vigente + supplier_bridge monitorado.

Toda R5/R6 que valida campo do WF contra contrato precisa do mesmo JOIN
base: pagamento → empreiteira → master monitorado → version vigente em
data_pedido. Centraliza aqui pra evitar drift.

universe_filter é injetado como WHERE fragment (SDD §9 v1.1.1):
    status_os IN ('EXECUTADO','EM EXECUÇÃO')
    AND nivel_gerencial IN ('Em Pagamento','Medido')
    AND malogro <> 'ERROR'

Match supplier ↔ wf é por LOWER(empreiteira) — Pré-B confirma 210 valores
únicos em WF vs 147 monitorados; case-insensitive reduz mismatch trivial.
"""

from __future__ import annotations


def wf_vigente_join_sql(
    wf_fields: str,
    cv_fields: str = "",
    extra_joins: str = "",
    extra_where: str = "",
    universe_filter: str = "",
) -> str:
    """Constrói SELECT WF × supplier × master × version vigente.

    Args:
      wf_fields: cols do WF a selecionar (sem prefix `wf.`) — ex: "wf.uf AS wf_uf"
      cv_fields: cols do CV (com prefix) — ex: "cv.uf AS cv_uf"
      extra_joins: JOINs adicionais (ex: LEFT JOIN purchase_order_gc)
      extra_where: predicados adicionais
      universe_filter: filtro universal (de ctx.universe_filter)
    """
    where_clause = f"({universe_filter})" if universe_filter else "TRUE"
    if extra_where:
        where_clause = f"{where_clause} AND ({extra_where})"

    cv_select = f", {cv_fields}" if cv_fields else ""

    return f"""
        SELECT
            wf.id              AS wf_id,
            wf.data_pedido     AS wf_data_pedido,
            wf.pedido_num      AS wf_pedido_num,
            wf.contrato_num    AS wf_contrato_num,
            wf.empreiteira     AS wf_empreiteira,
            sb.id::text        AS supplier_id,
            sb.categoria       AS supplier_categoria,
            cm.id::text        AS contract_master_id,
            cv.id::text        AS contract_version_id,
            {wf_fields}
            {cv_select}
        FROM payments.wf_payment wf
        JOIN payments.supplier_bridge sb
          ON LOWER(sb.empreiteira) = LOWER(wf.empreiteira)
        JOIN payments.contract_master cm
          ON cm.supplier_bridge_id = sb.id
         AND cm.is_monitored = TRUE
        JOIN payments.contract_version cv
          ON cv.contract_master_id = cm.id
         AND wf.data_pedido BETWEEN cv.valid_from AND cv.valid_to
        {extra_joins}
        WHERE {where_clause}
    """

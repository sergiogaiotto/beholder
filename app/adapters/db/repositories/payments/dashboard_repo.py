"""Repo de leitura para o Dashboard Empreiteiras-WF (Fase 3 Bloco B).

Concentra as 9 queries dos KPIs do print 2 + auxiliares para charts (Bloco C)
e tabela de fornecedores (Bloco D). Apenas leitura — não escreve. Usa o pool
dedicado `payments` (vide memory/payments_pool_quirks.md).

Convenção retorno: cada método devolve `dict` simples (chaves primitivas),
sem domain models. O `PaymentsDashboardService` formata para o template.
Valores numéricos vêm `Decimal` (asyncpg) ou `int` — o caller converte.

Filtro universal (SDD §9 v1.1.1 prefácio) — alinhado com `_base.py` do
rules engine. Mantido aqui via helper para que mudança futura propague:

  status_os IN ('EXECUTADO', 'EM EXECUÇÃO')
  AND nivel_gerencial IN ('Em Pagamento', 'Medido')
  AND malogro <> 'ERROR'
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.adapters.db.postgres_payments import connect_payments


# Filtro universal — duplicado do rules engine (_base.py:_UNIVERSE_FILTER_SQL)
# de propósito, para o dashboard não depender do módulo de regras. Sincronize
# os dois quando ajustar.
_UNIVERSE_FILTER_SQL = """
    status_os IN ('EXECUTADO', 'EM EXECUÇÃO')
    AND nivel_gerencial IN ('Em Pagamento', 'Medido')
    AND malogro <> 'ERROR'
"""

# Findings considerados "abertos" para fins de KPI — exclui aceitos como FP
# e bloqueados. Workflow segue migration 005 (CHECK status IN ...).
_OPEN_STATUSES = ("open", "in_analysis", "escalated")


class PaymentsDashboardRepository:
    """Queries somente-leitura para o dashboard. Stateless."""

    # ---------------------------------------------------------------- KPI 1+2
    async def kpi_contratos(self) -> dict[str, int]:
        """Contratos monitorados vs não monitorados + fornecedores únicos
        monitorados (alimenta o KPI 'CONTRATOS MONITORADOS' e o resumo
        executivo do header)."""
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE is_monitored)                 AS monitorados,
                    COUNT(*) FILTER (WHERE NOT is_monitored)             AS nao_monitorados,
                    COUNT(DISTINCT supplier_bridge_id) FILTER (WHERE is_monitored)
                                                                         AS fornecedores
                FROM payments.contract_master
                """
            )
            return {
                "monitorados": int(row["monitorados"] or 0),
                "nao_monitorados": int(row["nao_monitorados"] or 0),
                "fornecedores": int(row["fornecedores"] or 0),
            }

    # -------------------------------------------------------------------- KPI 3
    async def kpi_os(self) -> dict[str, int]:
        """OS distintas dentro do universo de regras + fornecedores únicos
        que aparecem nessas OS."""
        async with connect_payments() as c:
            row = await c.fetchrow(
                f"""
                SELECT
                    COUNT(DISTINCT os_num)         AS os_count,
                    COUNT(DISTINCT empreiteira)    AS fornecedores
                FROM payments.wf_payment
                WHERE {_UNIVERSE_FILTER_SQL}
                """
            )
            return {
                "os_count": int(row["os_count"] or 0),
                "fornecedores": int(row["fornecedores"] or 0),
            }

    # -------------------------------------------------------------------- KPI 4
    async def kpi_alertas_resumo(self) -> dict[str, Any]:
        """Total de findings abertos + risco financeiro somado + total
        analisado (universo wf_payment). Agrega tudo numa única round-trip
        ao pool para reduzir latência."""
        async with connect_payments() as c:
            row_findings = await c.fetchrow(
                """
                SELECT
                    COUNT(*)                                             AS total,
                    COALESCE(SUM(value_at_risk_brl), 0)::numeric         AS risco_brl
                FROM payments.reconciliation_finding
                WHERE status = ANY($1::text[])
                """,
                list(_OPEN_STATUSES),
            )
            row_universe = await c.fetchrow(
                f"""
                SELECT COALESCE(SUM(valor_total_final), 0)::numeric AS total_brl
                FROM payments.wf_payment
                WHERE {_UNIVERSE_FILTER_SQL}
                """
            )
            return {
                "total": int(row_findings["total"] or 0),
                "risco_brl": Decimal(row_findings["risco_brl"] or 0),
                "total_analisado_brl": Decimal(row_universe["total_brl"] or 0),
            }

    # -------------------------------------------------------------------- KPI 5+6
    async def kpi_lpu_resumo(self) -> dict[str, Any]:
        """Comparativo LPU = soma dos value_at_risk_brl de findings das
        regras R3 (texto/preço) e LPU (preço pago > LPU). Δ médio é a média
        do delta_pct dessas regras."""
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT
                    COALESCE(SUM(value_at_risk_brl), 0)::numeric  AS total_brl,
                    AVG(delta_pct)                                AS delta_pct_avg
                FROM payments.reconciliation_finding
                WHERE rule_code IN ('REGRA_3', 'REGRA_LPU')
                  AND status = ANY($1::text[])
                """,
                list(_OPEN_STATUSES),
            )
            avg = row["delta_pct_avg"]
            return {
                "total_brl": Decimal(row["total_brl"] or 0),
                "delta_pct_avg": float(avg) if avg is not None else None,
            }

    # -------------------------------------------------------------------- KPI 7
    async def kpi_recorrencia(self) -> dict[str, int]:
        """Fornecedores com >3 findings abertos / total fornecedores
        monitorados. Retorna os dois números; service divide."""
        async with connect_payments() as c:
            recorrentes = await c.fetchval(
                """
                SELECT COUNT(*) FROM (
                    SELECT supplier_id
                    FROM payments.reconciliation_finding
                    WHERE status = ANY($1::text[]) AND supplier_id IS NOT NULL
                    GROUP BY supplier_id
                    HAVING COUNT(*) > 3
                ) sub
                """,
                list(_OPEN_STATUSES),
            )
            total_monitorados = await c.fetchval(
                """
                SELECT COUNT(DISTINCT supplier_bridge_id)
                FROM payments.contract_master
                WHERE is_monitored
                """
            )
            return {
                "recorrentes": int(recorrentes or 0),
                "total_monitorados": int(total_monitorados or 0),
            }

    # -------------------------------------------------------------------- KPI 8
    async def kpi_tempo_deteccao(self) -> dict[str, float | None]:
        """Tempo médio (em dias) entre data_pedido do pagamento e a
        detecção do finding. NULL se não houver findings com
        wf_payment_data_pedido populado."""
        async with connect_payments() as c:
            avg_days = await c.fetchval(
                """
                SELECT AVG(
                    EXTRACT(EPOCH FROM (detected_at - wf_payment_data_pedido::timestamp))
                    / 86400.0
                )
                FROM payments.reconciliation_finding
                WHERE wf_payment_data_pedido IS NOT NULL
                """
            )
            return {"avg_dias": float(avg_days) if avg_days is not None else None}

    # -------------------------------------------------------------------- KPI 9
    async def kpi_acuracidade(self) -> dict[str, int]:
        """Conta regras únicas que rodaram com sucesso (apareceram em
        reconciliation_run.rules_executed onde status='completed').
        O denominador (target) é fixo no service — vide TOTAL_RULES_TARGET."""
        async with connect_payments() as c:
            executed_ok = await c.fetchval(
                """
                SELECT COUNT(DISTINCT unnested) FROM (
                    SELECT UNNEST(rules_executed) AS unnested
                    FROM payments.reconciliation_run
                    WHERE status = 'completed'
                ) sub
                """
            )
            return {"executed_ok": int(executed_ok or 0)}

    # =========================================================== Charts (Bloco C)

    async def chart_alertas_por_tipo(self) -> list[dict[str, Any]]:
        """Distribuição de findings abertos por 'tipo' derivado da severity.

        Mapeamento (provisório, alinhado com legenda do mockup):
          high   → 'Alerta Op.'    (operacional — exige ação)
          medium → 'Alerta Proc.'  (processo — investigar)
          low    → 'St. Atípica'   (situação atípica — monitorar)

        Sempre devolve as 3 categorias na ordem do mockup, mesmo com 0
        findings, para que o donut tenha legenda estável entre runs.
        """
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT severity, COUNT(*) AS qtd
                FROM payments.reconciliation_finding
                WHERE status = ANY($1::text[])
                GROUP BY severity
                """,
                list(_OPEN_STATUSES),
            )
        counts = {r["severity"]: int(r["qtd"]) for r in rows}
        return [
            {"tipo": "Alerta Op.", "qtd": counts.get("high", 0)},
            {"tipo": "Alerta Proc.", "qtd": counts.get("medium", 0)},
            {"tipo": "St. Atípica", "qtd": counts.get("low", 0)},
        ]

    async def chart_top_fornecedores(self, limit: int = 5) -> list[dict[str, Any]]:
        """Top-N empreiteiras por # de findings abertos, com breakdown por
        severity (stack do bar chart).

        Ranking: peso 3 para high, 2 para medium, 1 para low (privilegia
        operacionais sem deixar de incluir quem só tem atípicas)."""
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT
                    COALESCE(sb.empreiteira, '—') AS empreiteira,
                    COUNT(*) FILTER (WHERE rf.severity = 'high')   AS alerta_op,
                    COUNT(*) FILTER (WHERE rf.severity = 'medium') AS alerta_proc,
                    COUNT(*) FILTER (WHERE rf.severity = 'low')    AS st_atipica
                FROM payments.reconciliation_finding rf
                LEFT JOIN payments.supplier_bridge sb ON sb.id = rf.supplier_id
                WHERE rf.status = ANY($1::text[])
                GROUP BY sb.empreiteira
                ORDER BY (
                    COUNT(*) FILTER (WHERE rf.severity = 'high') * 3 +
                    COUNT(*) FILTER (WHERE rf.severity = 'medium') * 2 +
                    COUNT(*) FILTER (WHERE rf.severity = 'low')
                ) DESC
                LIMIT $2
                """,
                list(_OPEN_STATUSES),
                limit,
            )
        return [
            {
                "empreiteira": r["empreiteira"],
                "alerta_op": int(r["alerta_op"]),
                "alerta_proc": int(r["alerta_proc"]),
                "st_atipica": int(r["st_atipica"]),
            }
            for r in rows
        ]

    # ========================================================== Tabela + filtros (Bloco D)

    async def list_fornecedores(
        self,
        *,
        search: str | None = None,
        uf: str | None = None,
        severity: str | None = None,
    ) -> list[dict[str, str]]:
        """Tabela 'Visão por Fornecedor' (linhas únicas por empreiteira).

        Mostra apenas empreiteiras com contract_master.is_monitored = TRUE.
        Região é concatenação alfabética das UFs distintas que aparecem
        em wf_payment desse fornecedor (ou '—' se sem pagamentos).

        Filtros (todos opcionais e combináveis):
          - search: ILIKE em empreiteira ou cnpj
          - uf: empreiteira tem ao menos 1 wf_payment com essa UF
          - severity: empreiteira tem ao menos 1 finding aberto dessa
            severity (mapeamento severity↔tipo é feito no service)
        """
        where = ["cm.is_monitored"]
        params: list[Any] = []
        if search:
            params.append(f"%{search}%")
            params.append(f"%{search}%")
            i1 = len(params) - 1
            i2 = len(params)
            where.append(f"(sb.empreiteira ILIKE ${i1} OR sb.cnpj ILIKE ${i2})")
        if uf:
            params.append(uf)
            i = len(params)
            where.append(
                f"EXISTS (SELECT 1 FROM payments.wf_payment wf "
                f"WHERE wf.empreiteira = sb.empreiteira AND wf.uf = ${i})"
            )
        if severity:
            params.append(severity)
            params.extend(list(_OPEN_STATUSES))
            i_sev = len(params) - len(_OPEN_STATUSES)
            i_st = len(params) - len(_OPEN_STATUSES) + 1
            # status = ANY($i_st..) — usa array literal
            where.append(
                f"EXISTS (SELECT 1 FROM payments.reconciliation_finding rf "
                f"WHERE rf.supplier_id = sb.id "
                f"  AND rf.severity = ${i_sev} "
                f"  AND rf.status = ANY(ARRAY["
                + ", ".join(f"${i_st + k}" for k in range(len(_OPEN_STATUSES)))
                + "]::text[]))"
            )

        where_clause = " AND ".join(where)
        sql = f"""
            SELECT DISTINCT
                sb.empreiteira AS nome,
                sb.cnpj,
                COALESCE(
                    (SELECT STRING_AGG(DISTINCT wf.uf, ', ' ORDER BY wf.uf)
                     FROM payments.wf_payment wf
                     WHERE wf.empreiteira = sb.empreiteira AND wf.uf IS NOT NULL),
                    '—'
                ) AS regiao
            FROM payments.supplier_bridge sb
            JOIN payments.contract_master cm ON cm.supplier_bridge_id = sb.id
            WHERE {where_clause}
            ORDER BY sb.empreiteira
        """
        async with connect_payments() as c:
            rows = await c.fetch(sql, *params)
        return [
            {"nome": r["nome"], "cnpj": r["cnpj"], "regiao": r["regiao"] or "—"}
            for r in rows
        ]

    # =========================================================== Inbox /alertas (Bloco E)

    async def list_findings(
        self,
        *,
        severity: str | None = None,
        rule_code: str | None = None,
        status_in: tuple[str, ...] | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Lista paginada de findings com filtros opcionais.

        Devolve `{items, total, page, per_page, pages}`. Sempre faz JOIN
        com supplier_bridge para mostrar nome do fornecedor. Ordenação fixa
        em `detected_at DESC` (mais recente primeiro).

        Filtros:
          - severity: 'high' | 'medium' | 'low'
          - rule_code: 'REGRA_LPU', 'REGRA_5_UF', etc.
          - status_in: tupla de statuses (default = abertos)
          - search: ILIKE em rule_code ou purchase_order_documento
        """
        page = max(1, page)
        per_page = max(1, min(100, per_page))  # cap em 100 pra proteger pool
        offset = (page - 1) * per_page

        where = ["1=1"]
        params: list[Any] = []
        if severity:
            params.append(severity)
            where.append(f"rf.severity = ${len(params)}")
        if rule_code:
            params.append(rule_code)
            where.append(f"rf.rule_code = ${len(params)}")
        if status_in is None:
            status_in = _OPEN_STATUSES
        params.append(list(status_in))
        where.append(f"rf.status = ANY(${len(params)}::text[])")
        if search:
            params.append(f"%{search}%")
            i1 = len(params)
            params.append(f"%{search}%")
            i2 = len(params)
            where.append(
                f"(rf.rule_code ILIKE ${i1} OR rf.purchase_order_documento ILIKE ${i2})"
            )

        where_clause = " AND ".join(where)
        async with connect_payments() as c:
            total = await c.fetchval(
                f"""
                SELECT COUNT(*)
                FROM payments.reconciliation_finding rf
                WHERE {where_clause}
                """,
                *params,
            )
            params_with_pagination = [*params, per_page, offset]
            i_lim = len(params_with_pagination) - 1
            i_off = len(params_with_pagination)
            rows = await c.fetch(
                f"""
                SELECT
                    rf.id,
                    rf.rule_code,
                    rf.severity,
                    rf.status,
                    rf.purchase_order_documento,
                    rf.value_at_risk_brl,
                    rf.delta_pct,
                    rf.detected_at,
                    sb.empreiteira AS supplier_nome,
                    sb.cnpj        AS supplier_cnpj
                FROM payments.reconciliation_finding rf
                LEFT JOIN payments.supplier_bridge sb ON sb.id = rf.supplier_id
                WHERE {where_clause}
                ORDER BY rf.detected_at DESC, rf.id
                LIMIT ${i_lim} OFFSET ${i_off}
                """,
                *params_with_pagination,
            )

        items = [
            {
                "id": str(r["id"]),
                "rule_code": r["rule_code"],
                "severity": r["severity"],
                "status": r["status"],
                "purchase_order_documento": r["purchase_order_documento"],
                "value_at_risk_brl": Decimal(r["value_at_risk_brl"] or 0),
                "delta_pct": float(r["delta_pct"]) if r["delta_pct"] is not None else None,
                "detected_at": r["detected_at"],
                "supplier_nome": r["supplier_nome"] or "—",
                "supplier_cnpj": r["supplier_cnpj"] or "—",
            }
            for r in rows
        ]

        total_int = int(total or 0)
        pages = max(1, (total_int + per_page - 1) // per_page)
        # `rows` (em vez de `items`) evita colisão com `dict.items()` em Jinja
        # quando o template faz `findings.rows`.
        return {
            "rows": items,
            "total": total_int,
            "page": page,
            "per_page": per_page,
            "pages": pages,
        }

    # =========================================================== Detalhe finding (Bloco F)

    async def get_finding(self, finding_id: str) -> dict[str, Any] | None:
        """Detalhe completo de 1 finding com JOINs (rule, supplier, run,
        contract_master). Devolve `None` se UUID não existe.

        Faz unwrap dos JSONB `expected_value` / `actual_value` para o
        template renderizar como key/value table.
        """
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT
                    rf.id,
                    rf.rule_code,
                    rf.severity,
                    rf.status,
                    rf.purchase_order_documento,
                    rf.purchase_order_item,
                    rf.wf_payment_data_pedido,
                    rf.is_monitored_supplier,
                    rf.expected_value,
                    rf.actual_value,
                    rf.delta_pct,
                    rf.value_at_risk_brl,
                    rf.detected_at,
                    rf.decision_reason,
                    rf.decided_at,
                    rd.name           AS rule_name,
                    rd.description    AS rule_description,
                    rd.engine_type    AS rule_engine_type,
                    sb.id             AS supplier_id,
                    sb.empreiteira    AS supplier_nome,
                    sb.cnpj           AS supplier_cnpj,
                    sb.contrato_num_sap AS supplier_contrato_sap,
                    cm.id             AS contract_master_id,
                    rr.id             AS run_id,
                    rr.triggered_by   AS run_triggered_by,
                    rr.started_at     AS run_started_at,
                    u_decided.username AS decided_by_username
                FROM payments.reconciliation_finding rf
                LEFT JOIN payments.rule_definition rd  ON rd.id = rf.rule_id
                LEFT JOIN payments.supplier_bridge sb  ON sb.id = rf.supplier_id
                LEFT JOIN payments.contract_master cm  ON cm.id = rf.contract_master_id
                LEFT JOIN payments.reconciliation_run rr ON rr.id = rf.run_id
                LEFT JOIN users u_decided              ON u_decided.id = rf.decided_by_id
                WHERE rf.id = $1::uuid
                """,
                finding_id,
            )
            if row is None:
                return None
        # JSONB: o pool payments tem codec configurado (devolve dict direto);
        # mas em ambientes sem codec custom asyncpg devolve string — aceita
        # ambos os caminhos.
        import json

        def _maybe_parse_jsonb(v: Any) -> Any:
            if v is None:
                return {}
            if isinstance(v, (dict, list)):
                return v
            return json.loads(v)

        return {
            "id": str(row["id"]),
            "rule_code": row["rule_code"],
            "rule_name": row["rule_name"] or row["rule_code"],
            "rule_description": row["rule_description"] or "",
            "rule_engine_type": row["rule_engine_type"] or "",
            "severity": row["severity"],
            "status": row["status"],
            "purchase_order_documento": row["purchase_order_documento"],
            "purchase_order_item": row["purchase_order_item"],
            "wf_payment_data_pedido": row["wf_payment_data_pedido"],
            "is_monitored_supplier": row["is_monitored_supplier"],
            "expected_value": _maybe_parse_jsonb(row["expected_value"]),
            "actual_value": _maybe_parse_jsonb(row["actual_value"]),
            "delta_pct": float(row["delta_pct"]) if row["delta_pct"] is not None else None,
            "value_at_risk_brl": Decimal(row["value_at_risk_brl"] or 0),
            "detected_at": row["detected_at"],
            "decision_reason": row["decision_reason"],
            "decided_at": row["decided_at"],
            "decided_by_username": row["decided_by_username"],
            "supplier_id": str(row["supplier_id"]) if row["supplier_id"] else None,
            "supplier_nome": row["supplier_nome"] or "—",
            "supplier_cnpj": row["supplier_cnpj"] or "—",
            "supplier_contrato_sap": row["supplier_contrato_sap"] or "—",
            "contract_master_id": str(row["contract_master_id"]) if row["contract_master_id"] else None,
            "run_id": str(row["run_id"]) if row["run_id"] else None,
            "run_triggered_by": row["run_triggered_by"],
            "run_started_at": row["run_started_at"],
        }

    async def update_finding_status(
        self,
        finding_id: str,
        *,
        new_status: str,
        decision_reason: str | None,
        decided_by_user_id: str | None,
    ) -> bool:
        """Atualiza status + decision_reason + decided_by + decided_at.
        Returns True se atualizou, False se finding inexistente."""
        async with connect_payments() as c:
            result = await c.execute(
                """
                UPDATE payments.reconciliation_finding
                SET status = $2,
                    decision_reason = $3,
                    decided_by_id = $4::uuid,
                    decided_at = NOW()
                WHERE id = $1::uuid
                """,
                finding_id,
                new_status,
                decision_reason,
                decided_by_user_id,
            )
        # asyncpg.execute devolve string tipo 'UPDATE 1'
        return result.endswith(" 1")

    async def list_rule_codes_with_findings(self) -> list[str]:
        """rule_codes únicos que têm pelo menos 1 finding (qualquer status).
        Alimenta o dropdown 'Regra' do inbox de alertas."""
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT DISTINCT rule_code
                FROM payments.reconciliation_finding
                ORDER BY rule_code
                """
            )
        return [r["rule_code"] for r in rows]

    async def list_ufs_disponiveis(self) -> list[str]:
        """UFs distintas presentes em wf_payment — alimenta o dropdown
        de filtro 'Estado'. Excluindo NULL e ordem alfabética."""
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT DISTINCT uf
                FROM payments.wf_payment
                WHERE uf IS NOT NULL
                ORDER BY uf
                """
            )
        return [r["uf"] for r in rows]

    async def chart_risco_financeiro(self, limit: int = 5) -> list[dict[str, Any]]:
        """Top-N empreiteiras por soma de value_at_risk_brl em findings
        abertos. Ordem descendente — primeira linha é a mais arriscada."""
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT
                    COALESCE(sb.empreiteira, '—') AS empreiteira,
                    COALESCE(SUM(rf.value_at_risk_brl), 0)::numeric AS risco_brl
                FROM payments.reconciliation_finding rf
                LEFT JOIN payments.supplier_bridge sb ON sb.id = rf.supplier_id
                WHERE rf.status = ANY($1::text[])
                GROUP BY sb.empreiteira
                ORDER BY risco_brl DESC
                LIMIT $2
                """,
                list(_OPEN_STATUSES),
                limit,
            )
        return [
            {
                "empreiteira": r["empreiteira"],
                "risco_brl": Decimal(r["risco_brl"]),
            }
            for r in rows
        ]

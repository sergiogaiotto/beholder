"""Use case: Dashboard Empreiteiras-WF (Fase 3 — UI).

Centraliza a montagem do payload do template `payments/empreiteiras_wf/index.html`:
header executivo, 9 KPIs, 3 séries para Chart.js e tabela de fornecedores.

Estado por bloco:
  - Bloco A: stub com mock data.
  - **Bloco B (atual)**: 9 KPIs vêm do `PaymentsDashboardRepository` (queries
    reais no schema `payments`). Charts e fornecedores seguem mock.
  - Bloco C: charts viram queries reais.
  - Bloco D: fornecedores idem + filtros HTMX.

Decisões fixas (vide memory/current_state.md, Fase 3 escopo):
  - Acuracidade = `executed_ok / TOTAL_RULES_TARGET`, com `TOTAL_RULES_TARGET = 36`
    (20 handlers atuais + 11 R7 Fase 2.5 + 5 placeholder). TODO: trocar
    pela contagem dinâmica do rule_definition quando R7 entrar.
  - Cards são `<a href>` que pré-filtram `/payments/empreiteiras-wf/alertas`
    (drill-down sem JS).
  - DB vazio → KPIs mostram zeros/percentuais zerados; "—" só para médias
    sem amostra (avg_dias, delta_pct_avg).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from app.adapters.db.repositories.payments.dashboard_repo import (
    PaymentsDashboardRepository,
)


# Fórmula provisória do KPI Acuracidade. Atualizar quando Fase 2.5 entregar
# os 11 detectores R7 (vira COUNT(*) FROM payments.rule_definition WHERE active).
TOTAL_RULES_TARGET = 36  # 20 (R1-R6.9+LPU) + 11 (R7_*) + 5 (placeholder)


# ---------------------------------------------------------------------------
# Formatadores BRL/percent — locais ao módulo para evitar dep externa.
# ---------------------------------------------------------------------------


def _fmt_brl(value: Decimal | float | int) -> str:
    """Decimal/float → 'R$ 1.234,56' (formato pt-BR). Negativo aceito.

    Sem dependência de `locale` (problemático em containers minimal).
    """
    v = Decimal(value)
    s = f"{v:,.2f}"  # '1,234,567.89'
    # Inverte separadores: vírgula→ponto (milhar), ponto→vírgula (decimal).
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def _fmt_pct(value: float, *, signed: bool = False, decimals: int = 1) -> str:
    """0.1234 (fração) → '12.3%'; aceita `signed=True` para Δ ('+105.3%')."""
    pct = value * 100.0
    sign = "+" if signed and pct >= 0 else ""
    return f"{sign}{pct:.{decimals}f}%"


def _safe_pct(numerator: float | Decimal, denominator: float | Decimal) -> float | None:
    """numerador/denominador como fração (0..1). None se denominador zero."""
    d = float(denominator)
    if d == 0.0:
        return None
    return float(numerator) / d


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PaymentsDashboardService:
    """Use case: monta o payload de dashboard de pagamentos Empreiteiras-WF.

    Bloco B: KPIs reais via `PaymentsDashboardRepository`. Charts e
    fornecedores ainda mock (substituídos nos Blocos C/D).
    """

    def __init__(self, repo: PaymentsDashboardRepository | None = None):
        self.repo = repo or PaymentsDashboardRepository()

    # =========================================================== KPIs reais
    async def _fetch_kpi_buckets(self) -> dict[str, Any]:
        """Dispara as 7 queries de KPI em paralelo via `asyncio.gather`.

        Pool dedicado payments tem max=20 (vide memory/payments_pool_quirks.md);
        7 conexões simultâneas estão bem dentro do orçamento. Latência cai
        do somatório para o pior caso individual (~50ms em dev local).
        """
        (
            contratos,
            os_,
            alertas,
            lpu,
            recorrencia,
            tempo,
            acuracidade,
        ) = await asyncio.gather(
            self.repo.kpi_contratos(),
            self.repo.kpi_os(),
            self.repo.kpi_alertas_resumo(),
            self.repo.kpi_lpu_resumo(),
            self.repo.kpi_recorrencia(),
            self.repo.kpi_tempo_deteccao(),
            self.repo.kpi_acuracidade(),
        )
        return {
            "contratos": contratos,
            "os": os_,
            "alertas": alertas,
            "lpu": lpu,
            "recorrencia": recorrencia,
            "tempo": tempo,
            "acuracidade": acuracidade,
        }

    # ============================================================ Public API

    async def header_payload(self, buckets: dict[str, Any] | None = None) -> dict[str, Any]:
        """Header vermelho do print 2 com Resumo Executivo dinâmico.

        Aceita `buckets` opcional (vindo de `_fetch_kpi_buckets`) para
        evitar refetch quando chamado de `dashboard_payload`.
        """
        if buckets is None:
            buckets = await self._fetch_kpi_buckets()
        contratos = buckets["contratos"]
        os_ = buckets["os"]
        return {
            "title": "Monitoramento de Pagamentos para Empreiteiras - WF",
            "subtitle": "Claro S.A.",
            "resumo_executivo": {
                "fornecedores": contratos["fornecedores"],
                "contratos_analisados": contratos["monitorados"],
                "os_analisadas": os_["os_count"],
            },
        }

    async def kpis(self, buckets: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """9 cards do grid 3×3 (print 2), valores formatados.

        Estrutura inalterada do Bloco A — só o conteúdo virou dinâmico.
        """
        if buckets is None:
            buckets = await self._fetch_kpi_buckets()

        contratos = buckets["contratos"]
        os_ = buckets["os"]
        alertas = buckets["alertas"]
        lpu = buckets["lpu"]
        recorrencia = buckets["recorrencia"]
        tempo = buckets["tempo"]
        acuracidade = buckets["acuracidade"]

        base = "/payments/empreiteiras-wf/alertas"

        # % risco
        pct_risco = _safe_pct(alertas["risco_brl"], alertas["total_analisado_brl"])
        pct_risco_str = _fmt_pct(pct_risco) if pct_risco is not None else "—"
        pct_devidos_str = (
            _fmt_pct(1.0 - pct_risco) + " devidos" if pct_risco is not None else "—"
        )

        # Δ médio LPU
        delta_str = (
            _fmt_pct(lpu["delta_pct_avg"], signed=True)
            if lpu["delta_pct_avg"] is not None
            else "—"
        )

        # Taxa de recorrência
        pct_recorr = _safe_pct(
            recorrencia["recorrentes"], recorrencia["total_monitorados"]
        )
        pct_recorr_str = _fmt_pct(pct_recorr) if pct_recorr is not None else "—"

        # Tempo médio detecção
        avg_dias = tempo["avg_dias"]
        tempo_str = f"{avg_dias:.1f} dias" if avg_dias is not None else "—"

        # Acuracidade
        executed_ok = acuracidade["executed_ok"]
        acur_pct = executed_ok / TOTAL_RULES_TARGET if TOTAL_RULES_TARGET else 0.0
        acur_str = _fmt_pct(acur_pct)

        return [
            {
                "key": "contratos_monitorados",
                "label": "CONTRATOS MONITORADOS",
                "value": str(contratos["monitorados"]),
                "hint": f"Não monitorados: {contratos['nao_monitorados']}",
                "icon": "eye",
                "href": f"{base}?escopo=monitorados",
            },
            {
                "key": "os_analisadas",
                "label": "O.S. ANALISADAS",
                "value": str(os_["os_count"]),
                "hint": f"Fornecedores: {os_['fornecedores']}",
                "icon": "doc",
                "href": f"{base}?escopo=os",
            },
            {
                "key": "total_alertas",
                "label": "TOTAL DE ALERTAS",
                "value": str(alertas["total"]),
                "hint": "Inconsistências detectadas",
                "icon": "alert",
                "href": base,
            },
            {
                "key": "risco_financeiro",
                "label": "RISCO EXPOSIÇÃO FINANCEIRA",
                "value": _fmt_brl(alertas["risco_brl"]),
                "hint": f"Total analisado: {_fmt_brl(alertas['total_analisado_brl'])}",
                "icon": "money",
                "href": f"{base}?sort=risco_desc",
            },
            {
                "key": "pct_risco",
                "label": "% RISCO EXPOS. FINANCEIRA",
                "value": pct_risco_str,
                "hint": pct_devidos_str,
                "icon": "pie",
                "href": f"{base}?sort=risco_desc",
            },
            {
                "key": "comparativo_lpu",
                "label": "COMPARATIVO LPU",
                "value": _fmt_brl(lpu["total_brl"]),
                "hint": f"Δ médio LPU {delta_str}",
                "icon": "chart",
                "href": f"{base}?regra=LPU",
            },
            {
                "key": "taxa_recorrencia",
                "label": "TAXA DE RECORRÊNCIA",
                "value": pct_recorr_str,
                "hint": "Fornecedores com + de 3 alertas",
                "icon": "refresh",
                "href": f"{base}?recorrencia=1",
            },
            {
                "key": "tempo_medio",
                "label": "TEMPO MÉDIO DETECÇÃO",
                "value": tempo_str,
                "hint": "Meta: < 5 dias",
                "icon": "clock",
                "href": base,
            },
            {
                "key": "acuracidade",
                "label": "ACURACIDADE",
                "value": acur_str,
                "hint": f"Regras: {executed_ok}/{TOTAL_RULES_TARGET}",
                "icon": "check",
                "href": base,
            },
        ]

    # =========================================================== Charts (Bloco C)

    # Paleta dos charts — referenciada também pelo template via dataset.
    _CHART_COLORS = {
        "Alerta Op.":   "#DC2626",  # brand-600 — high
        "Alerta Proc.": "#F87171",  # brand-400 — medium
        "St. Atípica":  "#7F1D1D",  # brand-900 — low
    }
    # Gradient cores → vermelho saturado p/ desbotado, para o horizontal bar
    # do "Risco Financeiro por Fornecedor".
    _RISCO_GRADIENT = ["#DC2626", "#F87171", "#FCA5A5", "#FECACA", "#FEE2E2"]

    async def _fetch_chart_buckets(self) -> dict[str, Any]:
        """Dispara as 3 queries de charts em paralelo."""
        alertas_por_tipo, top_fornecedores, risco_fin = await asyncio.gather(
            self.repo.chart_alertas_por_tipo(),
            self.repo.chart_top_fornecedores(),
            self.repo.chart_risco_financeiro(),
        )
        return {
            "alertas_por_tipo": alertas_por_tipo,
            "top_fornecedores": top_fornecedores,
            "risco_financeiro": risco_fin,
        }

    async def charts(self, chart_buckets: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
        """Payload dos 3 charts do print 1 (Chart.js 4.x).

        Aceita `chart_buckets` opcional (vindo de `_fetch_chart_buckets`)
        para evitar refetch quando chamado de `dashboard_payload`. Sempre
        devolve o shape esperado pelo template — listas vazias se DB vazio.
        """
        if chart_buckets is None:
            chart_buckets = await self._fetch_chart_buckets()

        apt = chart_buckets["alertas_por_tipo"]
        tf = chart_buckets["top_fornecedores"]
        risco = chart_buckets["risco_financeiro"]

        # Alertas por Tipo (donut) — ordem fixa pra estabilidade visual.
        apt_labels = [r["tipo"] for r in apt]
        apt_data = [r["qtd"] for r in apt]
        apt_colors = [self._CHART_COLORS[t] for t in apt_labels]

        # Top Fornecedores (bar stacked). Cada série é uma severidade.
        tf_labels = [r["empreiteira"] for r in tf]
        tf_series = [
            {
                "name": "Alerta Op.",
                "data": [r["alerta_op"] for r in tf],
                "color": self._CHART_COLORS["Alerta Op."],
            },
            {
                "name": "Alerta Proc.",
                "data": [r["alerta_proc"] for r in tf],
                "color": self._CHART_COLORS["Alerta Proc."],
            },
            {
                "name": "St. Atípica",
                "data": [r["st_atipica"] for r in tf],
                "color": self._CHART_COLORS["St. Atípica"],
            },
        ]

        # Risco Financeiro (horizontal bar) — valores em float pro JS.
        risco_labels = [r["empreiteira"] for r in risco]
        risco_data = [float(r["risco_brl"]) for r in risco]
        # Cores ciclam pela paleta; recorta no tamanho dos dados.
        risco_colors = self._RISCO_GRADIENT[: max(len(risco_data), 1)]

        return {
            "alertas_por_tipo": {
                "labels": apt_labels,
                "data": apt_data,
                "colors": apt_colors,
            },
            "top_fornecedores": {
                "labels": tf_labels,
                "series": tf_series,
            },
            "risco_financeiro": {
                "labels": risco_labels,
                "data": risco_data,
                "colors": risco_colors,
            },
        }

    # =========================================================== Tabela + filtros (Bloco D)

    # Tipos de alerta exibidos no select de filtro do dashboard, mapeados
    # para o campo severity dos findings. Ordem do mockup.
    TIPOS_ALERTA = (
        ("Alerta Op.", "high"),
        ("Alerta Proc.", "medium"),
        ("St. Atípica", "low"),
    )

    async def fornecedores(
        self,
        *,
        search: str | None = None,
        uf: str | None = None,
        tipo: str | None = None,
    ) -> list[dict[str, str]]:
        """Tabela 'Visão por Fornecedor' com filtros opcionais.

        `tipo` aceita o label visível ('Alerta Op.', 'Alerta Proc.',
        'St. Atípica') e o service converte para severity ('high'/medium'/'low')
        antes de chamar o repo.
        """
        severity = None
        if tipo:
            sev_map = dict(self.TIPOS_ALERTA)
            severity = sev_map.get(tipo)  # None se label desconhecido → no-op
        return await self.repo.list_fornecedores(
            search=(search or None),
            uf=(uf or None),
            severity=severity,
        )

    # =========================================================== Inbox /alertas (Bloco E)

    # Mapeamento status interno → label visível na UI.
    STATUS_LABELS = {
        "open":         "Aberto",
        "in_analysis":  "Em Análise",
        "escalated":    "Escalado",
        "accepted_fp":  "Falso Positivo",
        "blocked":      "Bloqueado",
    }

    async def inbox_payload(
        self,
        *,
        severity: str | None = None,
        rule_code: str | None = None,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Payload do template `alertas.html`. Filtros e paginação.

        Se `status` for None, mostra apenas abertos (workflow ativo);
        passe 'all' para incluir aceitos/bloqueados/escalados (auditoria).
        """
        if status == "all":
            status_in = tuple(self.STATUS_LABELS.keys())
        elif status:
            status_in = (status,)
        else:
            status_in = None  # repo aplica _OPEN_STATUSES default

        # Catálogos + lista em paralelo (3 round-trips).
        findings_page, rule_codes, ufs = await asyncio.gather(
            self.repo.list_findings(
                severity=severity,
                rule_code=rule_code,
                status_in=status_in,
                search=search,
                page=page,
                per_page=per_page,
            ),
            self.repo.list_rule_codes_with_findings(),
            self.repo.list_ufs_disponiveis(),
        )

        # Enriquece cada item com formatação BR.
        for item in findings_page["rows"]:
            item["value_at_risk_brl_fmt"] = _fmt_brl(item["value_at_risk_brl"])
            item["delta_pct_fmt"] = (
                _fmt_pct(item["delta_pct"], signed=True)
                if item["delta_pct"] is not None
                else "—"
            )
            item["severity_label"] = {
                "high": "Alerta Op.",
                "medium": "Alerta Proc.",
                "low": "St. Atípica",
            }.get(item["severity"], item["severity"])
            item["status_label"] = self.STATUS_LABELS.get(
                item["status"], item["status"]
            )
            item["detected_at_fmt"] = (
                item["detected_at"].strftime("%d/%m/%Y %H:%M")
                if item["detected_at"]
                else "—"
            )

        return {
            "findings": findings_page,
            "filtros": {
                "rule_codes": rule_codes,
                "tipos_alerta": [label for label, _sev in self.TIPOS_ALERTA],
                "statuses": list(self.STATUS_LABELS.items()),
            },
            "active_filters": {
                "severity": severity or "",
                "rule_code": rule_code or "",
                "status": status or "",
                "search": search or "",
            },
        }

    async def filtros_disponiveis(self) -> dict[str, list[Any]]:
        """Catálogo para popular os dropdowns da barra de filtros do
        dashboard: UFs presentes em wf_payment + tipos de alerta fixos.
        """
        ufs = await self.repo.list_ufs_disponiveis()
        return {
            "ufs": ufs,
            "tipos_alerta": [label for label, _sev in self.TIPOS_ALERTA],
        }

    # ========================================================== Aggregator

    async def dashboard_payload(
        self,
        *,
        search: str | None = None,
        uf: str | None = None,
        tipo: str | None = None,
    ) -> dict[str, Any]:
        """Dispara KPIs + Charts + filtros + tabela em paralelo (12 queries
        total) e monta o dict completo consumido pelo template.

        Filtros (`search`/`uf`/`tipo`) afetam apenas a tabela 'Visão por
        Fornecedor' — KPIs e charts mostram sempre o panorama completo.
        Mudar isso ficaria stretch goal (cada KPI vira filterable).

        Pool dedicado payments tem max=20 (vide memory/payments_pool_quirks.md);
        12 conexões simultâneas estão dentro do orçamento (sobra para outras
        sessões http concorrentes)."""
        kpi_buckets, chart_buckets, fornecedores, filtros = await asyncio.gather(
            self._fetch_kpi_buckets(),
            self._fetch_chart_buckets(),
            self.fornecedores(search=search, uf=uf, tipo=tipo),
            self.filtros_disponiveis(),
        )
        return {
            "header": await self.header_payload(kpi_buckets),
            "kpis": await self.kpis(kpi_buckets),
            "charts": await self.charts(chart_buckets),
            "fornecedores": fornecedores,
            "filtros": filtros,
            "active_filters": {
                "search": search or "",
                "uf": uf or "",
                "tipo": tipo or "",
            },
        }

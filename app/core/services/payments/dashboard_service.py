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

    # =========================================================== Mock buckets

    async def charts(self) -> dict[str, dict[str, Any]]:
        """[Mock no Bloco B; Bloco C substitui por queries reais.]

        Payload dos 3 charts do print 1 — formato pronto para `Chart.js` 4.x.
        """
        return {
            "alertas_por_tipo": {
                "labels": ["Alerta Op.", "St. Atípica"],
                "data": [7, 2],
                "colors": ["#DC2626", "#7F1D1D"],
            },
            "top_fornecedores": {
                "labels": [
                    "ENGEMAN MNT",
                    "EQS ENGENHARIA",
                    "FFA INFRAESTRUTURA",
                    "WG PEREIRA",
                    "ABILITY TECNOLOGIA",
                ],
                "series": [
                    {"name": "Alerta Op.", "data": [3, 2, 1, 1, 1], "color": "#DC2626"},
                    {"name": "Alerta Proc.", "data": [0, 0, 1, 0, 0], "color": "#F87171"},
                    {"name": "St. Atípica", "data": [0, 0, 0, 0, 0], "color": "#7F1D1D"},
                ],
            },
            "risco_financeiro": {
                "labels": [
                    "FFA INFRAESTRUTURA",
                    "ABILITY TECNOLOGIA",
                    "WG PEREIRA",
                    "ENGEMAN MNT",
                    "EQS ENGENHARIA",
                ],
                "data": [380000.00, 65000.00, 2010.71, 0.00, 0.00],
            },
        }

    async def fornecedores(self) -> list[dict[str, str]]:
        """[Mock no Bloco B; Bloco D substitui por query real.]"""
        return [
            {"nome": "ENGEMAN MNT", "cnpj": "01731483000167", "regiao": "RJ/ES, SP"},
            {"nome": "EQS ENGENHARIA", "cnpj": "80464753000197", "regiao": "RS, NE, CONO"},
            {"nome": "FFA INFRAESTRUTURA", "cnpj": "08375450000170", "regiao": "MG, RJ/ES"},
            {"nome": "WG PEREIRA", "cnpj": "14113561000101", "regiao": "SP"},
            {"nome": "ABILITY TECNOLOGIA", "cnpj": "06127582000158", "regiao": "SP"},
        ]

    # ========================================================== Aggregator

    async def dashboard_payload(self) -> dict[str, Any]:
        """Dispara as 7 queries de KPI uma vez (gather) e monta o dict
        completo consumido pelo template. Reusa o resultado entre
        `header_payload` e `kpis` para evitar refetch."""
        buckets = await self._fetch_kpi_buckets()
        return {
            "header": await self.header_payload(buckets),
            "kpis": await self.kpis(buckets),
            "charts": await self.charts(),
            "fornecedores": await self.fornecedores(),
        }

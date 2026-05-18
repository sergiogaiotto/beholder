"""Use case: Dashboard Empreiteiras-WF (Fase 3 — UI).

Centraliza a montagem do payload do template `payments/empreiteiras_wf/index.html`:
header executivo, 9 KPIs, 3 séries para Chart.js e tabela de fornecedores.

**Bloco A — stub com mock data.** Retorna valores hard-coded que reproduzem
os mockups (CONTRATOS=12, O.S.=261, ALERTAS=9, RISCO=R$ 447.010,71, etc.).
Os blocos seguintes (B, C, D) substituem cada bucket por queries reais via
`PaymentsDashboardRepository` sem mexer no shape do dict, mantendo o template
estável durante toda a fase.

Decisões fixas (vide memory/current_state.md, Fase 3 escopo):
  - Acuracidade = `executed_ok / TOTAL_RULES`, com `TOTAL_RULES = 36`
    (20 handlers atuais + 11 R7 Fase 2.5 + 5 placeholder). TODO: trocar
    pela contagem dinâmica do rule_definition quando R7 entrar.
  - Cards são `<a href>` que pré-filtram `/payments/empreiteiras-wf/alertas`
    (drill-down sem JS).
"""

from __future__ import annotations

from typing import Any


# Fórmula provisória do KPI Acuracidade. Atualizar quando Fase 2.5 entregar
# os 11 detectores R7 (vira COUNT(*) FROM payments.rule_definition WHERE active).
TOTAL_RULES_TARGET = 36  # 20 (R1-R6.9+LPU) + 11 (R7_*) + 5 (placeholder)


class PaymentsDashboardService:
    """Use case: monta o payload de dashboard de pagamentos Empreiteiras-WF.

    No Bloco A devolve dados mock idênticos aos do mockup para destravar o
    desenvolvimento do template. Métodos públicos (header_payload, kpis,
    charts, fornecedores) ganharão implementação real nos próximos blocos.
    """

    async def header_payload(self) -> dict[str, Any]:
        """Header vermelho do print 2: título, cliente, resumo executivo
        (3 contadores) e botoes de toolbar (placeholder no Bloco A)."""
        return {
            "title": "Monitoramento de Pagamentos para Empreiteiras - WF",
            "subtitle": "Claro S.A.",
            "resumo_executivo": {
                "fornecedores": 5,
                "contratos_analisados": 12,
                "os_analisadas": 261,
            },
        }

    async def kpis(self) -> list[dict[str, Any]]:
        """9 cards do grid 3×3 (print 2). Cada card é dict com:
          - key: identificador estável para filtros HTMX (blocos C/D)
          - label: texto uppercase mostrado no card
          - value: número/string já formatado para exibição
          - hint: linha pequena abaixo do valor
          - icon: chave do ícone SVG inline definido no template
          - href: drill-down para `/alertas?filter=...` (Bloco F conecta).
        """
        base = "/payments/empreiteiras-wf/alertas"
        return [
            {
                "key": "contratos_monitorados",
                "label": "CONTRATOS MONITORADOS",
                "value": "12",
                "hint": "Não monitorados: 125",
                "icon": "eye",
                "href": f"{base}?escopo=monitorados",
            },
            {
                "key": "os_analisadas",
                "label": "O.S. ANALISADAS",
                "value": "261",
                "hint": "Fornecedores: 5",
                "icon": "doc",
                "href": f"{base}?escopo=os",
            },
            {
                "key": "total_alertas",
                "label": "TOTAL DE ALERTAS",
                "value": "9",
                "hint": "Inconsistências detectadas",
                "icon": "alert",
                "href": base,
            },
            {
                "key": "risco_financeiro",
                "label": "RISCO EXPOSIÇÃO FINANCEIRA",
                "value": "R$ 447.010,71",
                "hint": "Total analisado: R$ 39.411.186,00",
                "icon": "money",
                "href": f"{base}?sort=risco_desc",
            },
            {
                "key": "pct_risco",
                "label": "% RISCO EXPOS. FINANCEIRA",
                "value": "1.1%",
                "hint": "98.9% devidos",
                "icon": "pie",
                "href": f"{base}?sort=risco_desc",
            },
            {
                "key": "comparativo_lpu",
                "label": "COMPARATIVO LPU",
                "value": "R$ 4.415.559,06",
                "hint": "Δ médio LPU +105.3%",
                "icon": "chart",
                "href": f"{base}?regra=LPU",
            },
            {
                "key": "taxa_recorrencia",
                "label": "TAXA DE RECORRÊNCIA",
                "value": "0.0%",
                "hint": "Fornecedores com + de 3 alertas",
                "icon": "refresh",
                "href": f"{base}?recorrencia=1",
            },
            {
                "key": "tempo_medio",
                "label": "TEMPO MÉDIO DETECÇÃO",
                "value": "0.5 dias",
                "hint": "Meta: < 5 dias",
                "icon": "clock",
                "href": base,
            },
            {
                "key": "acuracidade",
                "label": "ACURACIDADE",
                "value": "100.0%",
                "hint": f"Regras: {TOTAL_RULES_TARGET}/{TOTAL_RULES_TARGET}",
                "icon": "check",
                "href": base,
            },
        ]

    async def charts(self) -> dict[str, dict[str, Any]]:
        """Payload dos 3 charts do print 1.

        Formato pronto para `Chart.js` 4.x — labels/datasets já alinhados
        com a paleta `brand` (vermelhos do tema Beholder). Cores em hex
        explícito para o template não precisar resolver via Tailwind JIT
        em runtime.
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
        """Linhas da tabela 'Visão por Fornecedor' do print 1."""
        return [
            {"nome": "ENGEMAN MNT", "cnpj": "01731483000167", "regiao": "RJ/ES, SP"},
            {"nome": "EQS ENGENHARIA", "cnpj": "80464753000197", "regiao": "RS, NE, CONO"},
            {"nome": "FFA INFRAESTRUTURA", "cnpj": "08375450000170", "regiao": "MG, RJ/ES"},
            {"nome": "WG PEREIRA", "cnpj": "14113561000101", "regiao": "SP"},
            {"nome": "ABILITY TECNOLOGIA", "cnpj": "06127582000158", "regiao": "SP"},
        ]

    async def dashboard_payload(self) -> dict[str, Any]:
        """Conveniência: dispara as 4 chamadas e devolve o dict completo
        que o template `payments/empreiteiras_wf/index.html` consome.

        Sequencial no Bloco A (latência irrelevante pra mock). No Bloco B
        viramos `asyncio.gather` quando cada bucket virar query real.
        """
        return {
            "header": await self.header_payload(),
            "kpis": await self.kpis(),
            "charts": await self.charts(),
            "fornecedores": await self.fornecedores(),
        }

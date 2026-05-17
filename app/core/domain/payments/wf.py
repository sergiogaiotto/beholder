"""WFPayment — pagamentos analíticos WF1+WF2 (migration 004).

869.663 rows iniciais do Analítico WF (Pré-B confirmou cardinalidades).
v1.1.1: 30 colunas tipadas (após Pré-B descobrir 12 cols adicionais além
das previstas na v1.1) + raw_extra para ~50 opcionais.

Particionada por trimestre de data_pedido (2024Q4 → 2026Q2 + default).
data_pedido é NOT NULL — partition key.

Taxonomias controladas (Pré-B):
  - sistema:           2 vals (WF1, WF2)              → Sistema enum
  - tipo_de_despesa:   2 vals (CAPEX, OPEX)           → TipoDespesa enum
  - uf:                27 vals (estados BR)           → regex `^[A-Z]{2}$`
  - mes_medicao:       "YYYY/MM"                      → regex
  - regional_soe_nova: 6 vals                         → str livre (whitelist
                       embaixo, para validação opcional)

Filtros universais SDD §9 v1.1.1 (idx_wf_universe):
  status_os ∈ ('EXECUTADO','EM EXECUÇÃO')
  ∧ nivel_gerencial ∈ ('Em Pagamento','Medido')
  ∧ malogro ≠ 'ERROR'
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.domain.payments.base import Money, PaymentsBaseModel
from app.core.domain.payments.enums import Sistema, TipoDespesa

# Whitelist documental — Pré-B confirmou 6 valores. Não enforcamos como Enum
# porque pode crescer; usado em queries analytics e validação opcional.
ALLOWED_REGIONAL_SOE: frozenset[str] = frozenset(
    {"CONO", "MG", "NE", "RJ/ES", "SP", "SUL"}
)


class WFPayment(PaymentsBaseModel):
    """Pagamento analítico WF1/WF2. Fonte primária após DE-PARA (R6.5)."""

    id: int | None = None  # BIGSERIAL — None até INSERT

    # ── chaves de negócio ──────────────────────────────────────────────
    os_num: str
    sistema: Sistema | None = None
    pedido_num: str | None = None
    contrato_num: str | None = None
    item_num: str | None = None
    item_descricao: str | None = None
    material_servico_num: str | None = None  # 912 únicos; chave LPU

    data_pedido: date  # partition key — sempre obrigatório
    data_execucao: date | None = None

    # ── valores monetários ─────────────────────────────────────────────
    valor_total_final: Money | None = None  # pago após DE-PARA (R6.5)
    valor_unitario: Money | None = None
    valor_unitario_para: Money | None = None

    # ── escopo R5 (taxonomias controladas) ─────────────────────────────
    categoria: str | None = None  # 11 vals
    uf: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")  # 27 vals
    cidade: str | None = None  # ≥1k vals — texto livre
    tecnologia: str | None = None  # 35 vals
    atividade: str | None = None  # 56 vals
    objeto_do_contrato: str | None = None  # 598 vals (taxonomia, não texto livre)

    # ── tipos contratuais ──────────────────────────────────────────────
    tipo_de_lpu: str | None = None  # FIXO MENSAL / LPU MEDIÇÃO / LPU REFERENCIAL
    tipo_de_despesa: TipoDespesa | None = None

    # ── contexto operacional (filtro universal) ────────────────────────
    empreiteira: str | None = None  # 210 únicos (vs 147 monitoradas)
    fase_atual: str | None = None  # 34 vals
    status_os: str | None = None  # 5 vals — filtro: ('EXECUTADO','EM EXECUÇÃO')
    nivel_gerencial: str | None = None  # 5 vals — filtro: ('Em Pagamento','Medido')
    malogro: str | None = None  # filtro: != 'ERROR'

    # ── contexto financeiro/temporal ───────────────────────────────────
    mes_medicao: str | None = Field(
        default=None, pattern=r"^\d{4}/(0[1-9]|1[0-2])$"
    )
    regional_soe_nova: str | None = None  # 6 vals (CONO, MG, NE, RJ/ES, SP, SUL)
    centro_de_custo: str | None = None  # 360 únicos

    # ── catchall ───────────────────────────────────────────────────────
    raw_extra: dict[str, Any] = Field(default_factory=dict)
    ingestion_run_id: UUID | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

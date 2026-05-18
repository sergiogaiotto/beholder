"""Entidades projetadas dos XLSX SAP (migration 003).

  PurchaseOrderHeader  — EKKO (179 cols → 13 tipadas + raw_extra)
  PurchaseOrderItem    — EKPO (283 cols → 12 tipadas + raw_extra)
  ServicePackage       — ESLL (10 cols)
  PurchaseOrderGc      — sheet "Contratos Guarda Chuvas" (v1.1, tabela física)
  CostCenterAccount    — sheet "CC + CONTA" do Analítico WF (1.049 rows)

Todas têm `raw_extra: dict` exceto CostCenterAccount (trivial 2-cols).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from app.core.domain.payments.base import Money, PaymentsBaseModel, Quantity


class PurchaseOrderHeader(PaymentsBaseModel):
    """EKKO — cabeçalho de pedido de compra SAP."""

    id: UUID = Field(default_factory=uuid4)
    documento_compras: str
    empresa: str
    categoria_doc: str | None = None  # 'K' = guarda-chuva, 'F' = pedido
    tipo_doc: str | None = None
    fornecedor: str
    contrato_basico: str | None = None  # ref guarda-chuva (R6.3)
    data_documento: date | None = None
    inicio_validade: date | None = None
    fim_validade: date | None = None
    val_fix_cab: Money | None = None
    moeda: str = "BRL"
    status: str | None = None
    raw_extra: dict[str, Any] = Field(default_factory=dict)
    ingestion_run_id: UUID | None = None
    imported_at: datetime = Field(default_factory=datetime.utcnow)


class PurchaseOrderItem(PaymentsBaseModel):
    """EKPO — item de pedido de compra SAP."""

    id: UUID = Field(default_factory=uuid4)
    documento_compras: str
    item: str
    texto_breve: str | None = None
    material: str | None = None
    grupo_mercadorias: str | None = None
    quantidade: Quantity | None = None
    unidade_medida: str | None = None
    preco_liquido: Money | None = None
    valor_liquido: Money | None = None  # usado por R6.5
    centro: str | None = None
    categoria_item: str | None = None
    raw_extra: dict[str, Any] = Field(default_factory=dict)
    ingestion_run_id: UUID | None = None
    imported_at: datetime = Field(default_factory=datetime.utcnow)


class ServicePackage(PaymentsBaseModel):
    """ESLL — pacote de serviço SAP. Bate com LPUItem.numero_servico via R LPU."""

    id: UUID = Field(default_factory=uuid4)
    pacote: str
    linha: int = Field(ge=0)
    numero_servico: str
    texto_breve: str | None = None
    preco_bruto: Money | None = None
    qtd_solicitada: Quantity | None = None
    valor_solicitado: Money | None = None
    ekpo_documento: str | None = None
    ekpo_item: str | None = None
    raw_extra: dict[str, Any] = Field(default_factory=dict)
    ingestion_run_id: UUID | None = None
    imported_at: datetime = Field(default_factory=datetime.utcnow)


class PurchaseOrderGc(PaymentsBaseModel):
    """Cruzamento pré-processado EKPO+ESLL+LPU para guarda-chuvas (R6.6-6.9).

    v1.1: tabela física na Fase 1 (D1 aprovada); Fase 3 reavalia derivar matview.
    """

    id: UUID = Field(default_factory=uuid4)
    documento_compras: str  # R6.6 × WF.contrato_num
    item: str  # R6.7 × WF.item_num
    ult_modif_dia: date | None = None
    texto_breve: str | None = None  # R6.8 × WF.item_descricao
    empresa: str | None = None
    numero_pacote_ekpo: str | None = None
    pacote_esll: str | None = None
    inicio_validade: date | None = None
    fim_validade: date | None = None
    val_fix_cab: Money | None = None
    preco_bruto_lpu: Money | None = None  # R6.9 × WF.valor_unitario
    numero_servico: str | None = None
    texto_breve_servico: str | None = None
    raw_extra: dict[str, Any] = Field(default_factory=dict)
    ingestion_run_id: UUID | None = None
    imported_at: datetime = Field(default_factory=datetime.utcnow)


class CostCenterAccount(PaymentsBaseModel):
    """Mapping centro_de_custo ↔ conta_razao (sheet 'CC + CONTA', 1.049 rows)."""

    id: int | None = None  # SERIAL — None até INSERT
    centro_de_custo: str
    conta_razao: str
    ingestion_run_id: UUID | None = None
    imported_at: datetime = Field(default_factory=datetime.utcnow)

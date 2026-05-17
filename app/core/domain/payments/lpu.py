"""LPUItem — Lista de Preços Unitários (migration 002).

Particionada por ano em data_documento (2018-2026 + default).
v1.1: 3.1M linhas reais do MSRV5 (Pré-B confirmou distribuição anual,
pico em 2022 com 560k linhas).

Notas:
  - id é BIGSERIAL — None até o INSERT.
  - contract_version_id é opcional porque a carga inicial do MSRV5 importa
    linhas antes de existir ContractVersion associada; vinculação acontece
    em um passo posterior (LPU↔contrato matching).
  - data_documento NOT NULL: partition key, sempre obrigatório.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.domain.payments.base import (
    Money,
    NonNegInt,
    PaymentsBaseModel,
    Pct01,
    Quantity,
)
from app.core.domain.payments.enums import SourceType


class LPUItem(PaymentsBaseModel):
    """1 linha da Lista de Preços Unitários (extraída do MSRV5/PDF/XLSX)."""

    id: int | None = None  # BIGSERIAL — None até INSERT
    contract_version_id: UUID | None = None

    documento_compras: str
    item: NonNegInt | None = None
    numero_servico: str
    data_documento: date  # partition key

    preco_unitario: Money
    qtd_solicitada: Quantity | None = None
    moeda: str = "BRL"

    descricao: str | None = None
    texto_breve: str | None = None
    pagina_pdf: int | None = Field(default=None, ge=1)
    clausula_ref: str | None = None

    extracted_by_llm: bool = False
    confidence: Pct01 | None = None
    source: SourceType = SourceType.MSRV5

    raw_extra: dict[str, Any] = Field(default_factory=dict)
    ingestion_run_id: UUID | None = None

"""Contratos de empreiteiras + cláusulas (migration 002).

Entidades:
  SupplierBridge   — tabela-âncora DE-PARA (147 rows iniciais)
  ContractMaster   — contrato jurídico (cabeça)
  ContractVersion  — versão temporal (aditivos)
  ContractClause   — cláusulas com embedding pgvector(1536)

Decisões de design:
  - CNPJ aceita qualquer str: normalização/validação DV fica no parser/projection,
    não no domain. Razão: domain é o limite confiável; parser é fronteira ruidosa.
  - ContractMaster.current_version_id é Optional porque o INSERT vem antes da
    primeira ContractVersion (FK circular resolvida com UPDATE posterior).
  - ContractVersion.uf valida formato UF (2 letras maiúsculas) — gratuito e
    pega XLSX mal-formado cedo.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.core.domain.payments.base import (
    EmbeddingVector,
    Money,
    PaymentsBaseModel,
    Pct01,
    PosInt,
)


class SupplierBridge(PaymentsBaseModel):
    """DE-PARA contrato SAP ↔ REF WS ↔ CNPJ (XLSX Contratos-Empreteiras)."""

    id: UUID = Field(default_factory=uuid4)
    categoria: str
    empreiteira: str
    contrato_num_sap: str
    ref_ws: str
    numero_fornecedor_sap: str
    cnpj: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ContractMaster(PaymentsBaseModel):
    """Contrato jurídico (cabeça). Versionado via ContractVersion."""

    id: UUID = Field(default_factory=uuid4)
    supplier_bridge_id: UUID
    contrato_num_sap: str
    ref_ws: str
    cnpj: str
    # FK circular: master → version. Pode ser None na criação inicial;
    # populado após a primeira ContractVersion ser inserida (UPDATE).
    current_version_id: UUID | None = None
    is_monitored: bool = True
    created_by_id: UUID
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ContractVersion(PaymentsBaseModel):
    """Versão temporal de um contrato (cobre aditivos + extensões)."""

    id: UUID = Field(default_factory=uuid4)
    contract_master_id: UUID
    version_number: PosInt
    valid_from: date
    valid_to: date
    val_fix_cab: Money | None = None
    objeto_contrato: str | None = None
    tecnologia: str | None = None
    atividade: str | None = None
    uf: list[str] = Field(default_factory=list)
    cidade: list[str] = Field(default_factory=list)
    pdf_storage_key: str | None = None
    extracted_by_llm_model: str | None = None
    extracted_cost_brl: Money = Decimal("0")
    confidence_avg: Pct01 | None = None
    reviewed_by_id: UUID | None = None
    reviewed_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def _check_temporal_order(self) -> Self:
        if self.valid_from > self.valid_to:
            raise ValueError(
                f"valid_from ({self.valid_from}) must be <= valid_to ({self.valid_to})"
            )
        return self

    @model_validator(mode="after")
    def _check_uf_format(self) -> Self:
        for uf in self.uf:
            if not (len(uf) == 2 and uf.isalpha() and uf == uf.upper()):
                raise ValueError(
                    f"UF deve ser 2 letras maiúsculas (got {uf!r})"
                )
        return self


class ContractClause(PaymentsBaseModel):
    """Cláusula de contrato com embedding (Fase 4 — similarity search)."""

    id: UUID = Field(default_factory=uuid4)
    contract_version_id: UUID
    clausula_numero: str | None = None
    secao: str | None = None
    texto: str
    embedding: EmbeddingVector | None = None
    pagina_pdf: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=datetime.utcnow)

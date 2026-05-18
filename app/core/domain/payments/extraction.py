"""ExtractionJob — extração assíncrona de PDF (migration 005, Fase 4).

Alimentado pelo worker dramatiq. 1 PDF → 1 job → 1 ContractVersion (após
review humano em status='approved').

Default LLM v1.1: 'sabia-4' (Maritaca). Pré-C validou empiricamente
R$0.37/PDF com 86% accuracy.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from app.core.domain.payments.base import Money, NonNegInt, PaymentsBaseModel
from app.core.domain.payments.enums import ExtractionStatus


class ExtractionJob(PaymentsBaseModel):
    """Job assíncrono de extração de campos a partir de PDF."""

    id: UUID = Field(default_factory=uuid4)
    contract_master_id: UUID | None = None
    pdf_storage_key: str  # chave no DocumentStore
    pdf_filename: str
    pdf_size_bytes: NonNegInt
    pdf_pages: int | None = Field(default=None, ge=1)

    status: ExtractionStatus
    extraction_started_at: datetime | None = None
    extraction_finished_at: datetime | None = None

    # pré-aprove: folha de rosto + LPU items
    extracted_fields: dict[str, Any] | None = None
    confidence_per_field: dict[str, float] | None = None

    llm_model_used: str | None = None  # 'sabia-4' default v1.1
    cost_brl: Money = Decimal("0")
    error_message: str | None = None
    uploaded_by_id: UUID
    created_at: datetime = Field(default_factory=datetime.utcnow)

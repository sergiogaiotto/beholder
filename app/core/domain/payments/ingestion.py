"""IngestionRun — rastreabilidade de cargas externas (migration 001).

Cada job de ingestão (Polars carregando EKPO.xlsx, parser do MSRV5,
upload de PDF) cria um row. Linhas em wf_payment/lpu_item/etc apontam
de volta via ingestion_run_id — permite reverter/auditar uma carga
inteira sem afetar outras.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from app.core.domain.payments.base import NonNegInt, PaymentsBaseModel
from app.core.domain.payments.enums import IngestionStatus

# Valores que o parser/loader usa em source_type (não há CHECK no DB,
# mas documentamos a taxonomia aqui para fail-fast).
ALLOWED_SOURCE_TYPES: frozenset[str] = frozenset(
    {"xlsx", "msrv5_txt", "analitico_wf", "pdf"}
)


class IngestionRun(PaymentsBaseModel):
    """1 execução de ingestão externa → payments.*."""

    id: UUID = Field(default_factory=uuid4)
    source_type: str
    source_filename: str
    source_sha256: str | None = None
    source_size_bytes: NonNegInt | None = None
    target_table: str

    status: IngestionStatus = IngestionStatus.PENDING

    rows_read: NonNegInt = 0
    rows_inserted: NonNegInt = 0
    rows_skipped: NonNegInt = 0
    rows_failed: NonNegInt = 0

    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    error_message: str | None = None
    triggered_by_user_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_type")
    @classmethod
    def _validate_source_type(cls, v: str) -> str:
        if v not in ALLOWED_SOURCE_TYPES:
            raise ValueError(
                f"source_type {v!r} not in {sorted(ALLOWED_SOURCE_TYPES)}"
            )
        return v

    @field_validator("target_table")
    @classmethod
    def _validate_target_table(cls, v: str) -> str:
        if not v.startswith("payments."):
            raise ValueError(
                f"target_table must start with 'payments.', got {v!r}"
            )
        return v

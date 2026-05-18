"""Rules engine + reconciliação (migration 005).

  RuleDefinition         — catálogo das 20 regras (R1, R2, R3, R4,
                           6×R5, 9×R6, REGRA_LPU)
  ReconciliationRun      — 1 execução do engine
  ReconciliationFinding  — output: 1 violação de 1 regra
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from app.core.domain.payments.base import (
    Money,
    NonNegInt,
    PaymentsBaseModel,
    PosInt,
)
from app.core.domain.payments.enums import (
    EngineType,
    FindingStatus,
    RunStatus,
    Severity,
    TriggeredBy,
)


class RuleDefinition(PaymentsBaseModel):
    """Catálogo de regras determinísticas/fuzzy do rules_engine."""

    id: UUID = Field(default_factory=uuid4)
    code: str
    name: str
    description: str
    severity: Severity
    is_active: bool = True
    threshold_params: dict[str, Any] = Field(default_factory=dict)
    engine_type: EngineType
    python_handler: str  # dotted path: 'app.core.services.payments.rules.regra_X'
    version: PosInt = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ReconciliationRun(PaymentsBaseModel):
    """1 execução do engine de regras (manual, post-ingestion ou scheduled)."""

    id: UUID = Field(default_factory=uuid4)
    triggered_by: TriggeredBy
    triggered_by_user_id: UUID | None = None
    rules_executed: list[str] = Field(min_length=1)
    scope_filter: dict[str, Any] | None = None
    status: RunStatus
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    findings_created: NonNegInt = 0
    error_message: str | None = None


class ReconciliationFinding(PaymentsBaseModel):
    """1 violação de 1 regra contra 1 pagamento. Output principal do engine.

    v1.1.1:
      - wf_payment_id + wf_payment_data_pedido para join particionado
      - is_monitored_supplier: 63 empreiteiras NÃO monitoradas geram findings
        mas marcam-se aqui; UI tem filtro padrão TRUE.
    """

    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    rule_id: UUID
    rule_code: str  # denormalizado p/ query rápida
    severity: Severity
    status: FindingStatus = FindingStatus.OPEN

    # referências ao pagamento
    purchase_order_documento: str
    purchase_order_item: str | None = None
    wf_payment_id: int | None = None
    wf_payment_data_pedido: date | None = None  # DATE em DB

    # referências ao contrato
    contract_master_id: UUID | None = None
    contract_version_id: UUID | None = None
    supplier_id: UUID | None = None
    is_monitored_supplier: bool = True

    # corpo do finding
    expected_value: dict[str, Any]
    actual_value: dict[str, Any]
    delta_pct: float | None = None
    value_at_risk_brl: Money | None = None
    evidence_clause_ids: list[UUID] = Field(default_factory=list)
    evidence_pages: list[int] = Field(default_factory=list)

    # workflow HITL
    analyst_id: UUID | None = None
    decision_reason: str | None = None
    decided_by_id: UUID | None = None
    decided_at: datetime | None = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)

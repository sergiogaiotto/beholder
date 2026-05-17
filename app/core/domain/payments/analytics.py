"""Analytics R7 — catálogo + findings estatísticos (migration 006).

D2 aprovada: tabelas físicas separadas (não reutiliza rule_definition
nem reconciliation_finding) porque granularidade e semântica diferem.

11 detectores R7: LPU outlier, qtd quebrada, fixo/variável atípico,
pico fim período, empreiteira fora padrão, lag pagamento, períodos
atípicos, recorrência variável, consumo perfil, LPU padrão serviço,
validade vencida.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from app.core.domain.payments.base import PaymentsBaseModel, PosInt
from app.core.domain.payments.enums import (
    FindingStatus,
    Severity,
    Technique,
)


class AnalyticDetector(PaymentsBaseModel):
    """Catálogo dos 11 detectores R7 (análises estatísticas)."""

    id: UUID = Field(default_factory=uuid4)
    code: str
    name: str
    description: str
    technique: Technique
    severity: Severity
    is_active: bool = True
    threshold_params: dict[str, Any] = Field(default_factory=dict)
    python_handler: str
    version: PosInt = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AnalyticFinding(PaymentsBaseModel):
    """Output de 1 detector estatístico. Inbox separado de ReconciliationFinding.

    score é DOUBLE PRECISION (z-score, distância, ratio) — pode ser negativo.
    wf_payment_id pode ser None em findings agregados (ex.: empreiteira fora
    do padrão; o finding refere-se ao agregado, não a um pagamento individual).
    """

    id: UUID = Field(default_factory=uuid4)
    detector_id: UUID
    detector_code: str  # denormalizado
    severity: Severity

    wf_payment_id: int | None = None
    wf_payment_data_pedido: date | None = None
    supplier_id: UUID | None = None

    score: float  # NOT NULL — sempre presente
    expected_range: dict[str, Any]
    actual_value: dict[str, Any]
    evidence_payment_ids: list[int] = Field(default_factory=list)

    status: FindingStatus = FindingStatus.OPEN
    analyst_id: UUID | None = None
    decision_reason: str | None = None
    decided_by_id: UUID | None = None
    decided_at: datetime | None = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)

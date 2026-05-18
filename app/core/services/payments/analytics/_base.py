"""Tipos compartilhados pelos detectores R7:
  AnalyticFindingDraft  — dataclass leve que vira AnalyticFinding na persistência
  AnalyticContext       — passado a todo handler; carrega detector + DB access
  AnalyticHandler       — alias do callable async iterable

Espelha a estrutura de `rules/_base.py` para reduzir surpresa cognitiva entre
os dois engines. Diferenças:

  - Score (z-score, IQR distance, ratio) é obrigatório no draft — semântica
    estatística pede um número que sustente o "porquê" do desvio.
  - wf_payment_id é opcional (findings agregados — clustering, períodos).
  - expected_range vs actual_value: range carrega `{min, max, method}` pra
    UI explicar o limite cruzado.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.domain.payments import (
    AnalyticDetector,
    AnalyticFinding,
    FindingStatus,
    Severity,
)


@dataclass(slots=True)
class AnalyticFindingDraft:
    """Resultado intermediário de um handler R7. Vira AnalyticFinding via
    `.to_finding()`. Não persiste sozinho — o engine faz bulk_insert."""

    detector_code: str
    severity: Severity
    score: float
    expected_range: dict[str, Any]
    actual_value: dict[str, Any]

    # Referências opcionais (escopo varia por detector — clustering pode ser
    # agregado por supplier, sem payment individual).
    wf_payment_id: int | None = None
    wf_payment_data_pedido: date | None = None
    supplier_id: UUID | None = None
    evidence_payment_ids: list[int] = field(default_factory=list)

    # Informativo — não persistido (intra-run debugging / logs).
    reason: str | None = None

    def to_finding(self, *, detector_id: UUID) -> AnalyticFinding:
        """Converte para o domain model persistível."""
        return AnalyticFinding(
            id=uuid4(),
            detector_id=detector_id,
            detector_code=self.detector_code,
            severity=self.severity,
            wf_payment_id=self.wf_payment_id,
            wf_payment_data_pedido=self.wf_payment_data_pedido,
            supplier_id=self.supplier_id,
            score=self.score,
            expected_range=self.expected_range,
            actual_value=self.actual_value,
            evidence_payment_ids=self.evidence_payment_ids,
            status=FindingStatus.OPEN,
            detected_at=datetime.utcnow(),
        )


@dataclass(frozen=True)
class AnalyticContext:
    """Passado a todo handler R7. Carrega:
       - detector: AnalyticDetector do catálogo (id, code, threshold_params)
       - scope_filter: filtros opcionais (empreiteira, since, until)
       - universe_filter: SQL string do filtro universal — detectores PODEM
         honrar (ex: lpu outlier, lag pagto) ou IGNORAR (ex: validade vencida
         que olha contratos vencidos especificamente).
    """

    detector: AnalyticDetector
    scope_filter: dict[str, Any] | None = None
    universe_filter: str | None = None  # SQL WHERE fragment (sem `WHERE`)


# Filtro universal SDD §9 v1.1.1 — DUPLICATED de rules/_base.py de propósito.
# Detectores R7 podem ignorar (ex: validade vencida olha contratos fora do
# universo operacional ativo de propósito), por isso não é importado.
_UNIVERSE_FILTER_SQL = """
    status_os IN ('EXECUTADO', 'EM EXECUÇÃO')
    AND nivel_gerencial IN ('Em Pagamento', 'Medido')
    AND malogro <> 'ERROR'
"""


def universe_filter_for_detector(detector: AnalyticDetector) -> str:
    """Retorna o WHERE fragment a usar para `wf_payment`. Override via
    `detector.threshold_params.universe_filter`. Detectores que ignoram
    o filtro universal recebem string vazia (caller usa WHERE TRUE).
    """
    if detector.threshold_params.get("ignore_universe_filter"):
        return ""
    override = detector.threshold_params.get("universe_filter")
    if isinstance(override, str) and override.strip():
        return override
    return _UNIVERSE_FILTER_SQL.strip()


# Type alias para handlers — invocado pelo engine.
AnalyticHandler = Callable[[AnalyticContext], AsyncIterator[AnalyticFindingDraft]]

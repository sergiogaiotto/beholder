"""Tipos compartilhados pelos handlers de regra:
  FindingDraft       — dataclass leve que vira ReconciliationFinding na persistência
  ReconciliationContext — passado a todo handler; carrega run + rule_def + DB access
  RuleHandler        — alias do callable async iterable

FindingDraft é dataclass (não Pydantic) porque está no hot path: o engine
pode emitir 100k+ drafts por run. Pydantic overhead de validação não se
justifica aqui — os campos vêm do handler que já é confiável.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.core.domain.payments import (
    FindingStatus,
    ReconciliationFinding,
    ReconciliationRun,
    RuleDefinition,
    Severity,
)


@dataclass(slots=True)
class FindingDraft:
    """Resultado intermediário de um handler. Vira ReconciliationFinding via .to_finding()."""

    rule_code: str
    severity: Severity
    purchase_order_documento: str
    expected_value: dict[str, Any]
    actual_value: dict[str, Any]

    # Referências opcionais (escopo varia por regra)
    purchase_order_item: str | None = None
    wf_payment_id: int | None = None
    wf_payment_data_pedido: date | None = None
    contract_master_id: UUID | None = None
    contract_version_id: UUID | None = None
    supplier_id: UUID | None = None
    is_monitored_supplier: bool = True

    # Corpo extra
    delta_pct: float | None = None
    value_at_risk_brl: Decimal | None = None
    evidence_clause_ids: list[UUID] = field(default_factory=list)
    evidence_pages: list[int] = field(default_factory=list)

    # Informativo — não persistido (intra-run debugging / logs)
    reason: str | None = None

    def to_finding(
        self,
        *,
        run_id: UUID,
        rule_id: UUID,
    ) -> ReconciliationFinding:
        """Converte para o domain model persistível."""
        return ReconciliationFinding(
            id=uuid4(),
            run_id=run_id,
            rule_id=rule_id,
            rule_code=self.rule_code,
            severity=self.severity,
            status=FindingStatus.OPEN,
            purchase_order_documento=self.purchase_order_documento,
            purchase_order_item=self.purchase_order_item,
            wf_payment_id=self.wf_payment_id,
            wf_payment_data_pedido=self.wf_payment_data_pedido,
            contract_master_id=self.contract_master_id,
            contract_version_id=self.contract_version_id,
            supplier_id=self.supplier_id,
            is_monitored_supplier=self.is_monitored_supplier,
            expected_value=self.expected_value,
            actual_value=self.actual_value,
            delta_pct=self.delta_pct,
            value_at_risk_brl=self.value_at_risk_brl,
            evidence_clause_ids=self.evidence_clause_ids,
            evidence_pages=self.evidence_pages,
            detected_at=datetime.utcnow(),
        )


@dataclass(frozen=True)
class ReconciliationContext:
    """Passado a todo handler. Carrega:
       - run: ReconciliationRun em execução (status='running')
       - rule: RuleDefinition do catálogo (id, code, threshold_params)
       - scope_filter: filtros opcionais (empreiteira, since, until) — handler pode honrar
       - universe_filter: SQL string do filtro universal (§9 prefácio v1.1.1).
                         Default `_UNIVERSE_FILTER_SQL`; rule.threshold_params['universe_filter']
                         pode override.
    """

    run: ReconciliationRun
    rule: RuleDefinition
    scope_filter: dict[str, Any] | None = None
    universe_filter: str | None = None  # SQL WHERE fragment (sem o `WHERE`)


# Filtro universal SDD §9 v1.1.1 — todas as regras (R1-R6.9, LPU) usam.
# Mantido como string SQL para inlining em queries do handler.
_UNIVERSE_FILTER_SQL = """
    status_os IN ('EXECUTADO', 'EM EXECUÇÃO')
    AND nivel_gerencial IN ('Em Pagamento', 'Medido')
    AND malogro <> 'ERROR'
"""


def universe_filter_for(rule: RuleDefinition) -> str:
    """Retorna o WHERE fragment a usar para `wf_payment`. Override via
    `rule.threshold_params.universe_filter` (string SQL) — se ausente,
    usa o default global do §9 v1.1.1.
    """
    override = rule.threshold_params.get("universe_filter")
    if isinstance(override, str) and override.strip():
        return override
    return _UNIVERSE_FILTER_SQL.strip()


# Type alias para handlers — invocado pelo engine.
RuleHandler = Callable[[ReconciliationContext], AsyncIterator[FindingDraft]]

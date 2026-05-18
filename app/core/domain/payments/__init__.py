"""Domain models do bounded-context payments (Empreiteiras-WF).

Mapeamento 1:1 com tabelas das migrations 001-006:

  Migration 001 — ingestion.py
    IngestionRun

  Migration 002 — contracts.py + lpu.py
    SupplierBridge, ContractMaster, ContractVersion, ContractClause
    LPUItem (separado: particionada, central em R LPU)

  Migration 003 — sap.py
    PurchaseOrderHeader, PurchaseOrderItem, ServicePackage,
    PurchaseOrderGc, CostCenterAccount

  Migration 004 — wf.py
    WFPayment

  Migration 005 — rules.py + extraction.py
    RuleDefinition, ReconciliationRun, ReconciliationFinding
    ExtractionJob

  Migration 006 — analytics.py
    AnalyticDetector, AnalyticFinding

Total: 18 entidades (17 entities + IngestionRun da Fase 0/migration 001).

Convenção: importar diretamente do submódulo ou do pacote.
    from app.core.domain.payments import WFPayment, ContractVersion
    from app.core.domain.payments.wf import WFPayment  # equivalente
"""

from __future__ import annotations

from app.core.domain.payments.analytics import AnalyticDetector, AnalyticFinding
from app.core.domain.payments.base import (
    EmbeddingVector,
    Money,
    NonNegInt,
    PaymentsBaseModel,
    Pct01,
    PosInt,
    Quantity,
)
from app.core.domain.payments.contracts import (
    ContractClause,
    ContractMaster,
    ContractVersion,
    SupplierBridge,
)
from app.core.domain.payments.enums import (
    EngineType,
    ExtractionStatus,
    FindingStatus,
    IngestionStatus,
    RunStatus,
    Severity,
    Sistema,
    SourceType,
    Technique,
    TipoDespesa,
    TriggeredBy,
)
from app.core.domain.payments.extraction import ExtractionJob
from app.core.domain.payments.ingestion import IngestionRun
from app.core.domain.payments.lpu import LPUItem
from app.core.domain.payments.rules import (
    ReconciliationFinding,
    ReconciliationRun,
    RuleDefinition,
)
from app.core.domain.payments.sap import (
    CostCenterAccount,
    PurchaseOrderGc,
    PurchaseOrderHeader,
    PurchaseOrderItem,
    ServicePackage,
)
from app.core.domain.payments.wf import WFPayment

__all__ = [
    # base
    "PaymentsBaseModel",
    "Money",
    "Quantity",
    "Pct01",
    "NonNegInt",
    "PosInt",
    "EmbeddingVector",
    # enums
    "IngestionStatus",
    "Severity",
    "FindingStatus",
    "EngineType",
    "Technique",
    "SourceType",
    "ExtractionStatus",
    "RunStatus",
    "TriggeredBy",
    "Sistema",
    "TipoDespesa",
    # entities — 18 total
    "IngestionRun",
    "SupplierBridge",
    "ContractMaster",
    "ContractVersion",
    "ContractClause",
    "LPUItem",
    "PurchaseOrderHeader",
    "PurchaseOrderItem",
    "ServicePackage",
    "PurchaseOrderGc",
    "CostCenterAccount",
    "WFPayment",
    "RuleDefinition",
    "ReconciliationRun",
    "ReconciliationFinding",
    "AnalyticDetector",
    "AnalyticFinding",
    "ExtractionJob",
]

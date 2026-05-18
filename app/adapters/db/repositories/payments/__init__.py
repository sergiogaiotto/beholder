"""Implementações asyncpg dos repositórios do domínio payments.

Estrutura espelha `app/core/domain/payments/`:
  ingestion_repo.py    PgIngestionRunRepository
  contract_repos.py    PgSupplierBridge/Master/Version/ClauseRepository
  lpu_repo.py          PgLPUItemRepository
  sap_repos.py         5 SAP repos
  wf_repo.py           PgWFPaymentRepository
  rules_repos.py       PgRuleDefinition/ReconciliationRun/ReconciliationFindingRepository
  analytics_repos.py   PgAnalyticDetector/AnalyticFindingRepository
  extraction_repo.py   PgExtractionJobRepository

Todos usam `connect_payments()` do pool dedicado.
"""

from __future__ import annotations

from app.adapters.db.repositories.payments.analytics_repos import (
    PgAnalyticDetectorRepository,
    PgAnalyticFindingRepository,
)
from app.adapters.db.repositories.payments.contract_repos import (
    PgContractClauseRepository,
    PgContractMasterRepository,
    PgContractVersionRepository,
    PgSupplierBridgeRepository,
)
from app.adapters.db.repositories.payments.extraction_repo import (
    PgExtractionJobRepository,
)
from app.adapters.db.repositories.payments.ingestion_repo import (
    PgIngestionRunRepository,
)
from app.adapters.db.repositories.payments.lpu_repo import PgLPUItemRepository
from app.adapters.db.repositories.payments.rules_repos import (
    PgReconciliationFindingRepository,
    PgReconciliationRunRepository,
    PgRuleDefinitionRepository,
)
from app.adapters.db.repositories.payments.sap_repos import (
    PgCostCenterAccountRepository,
    PgPurchaseOrderGcRepository,
    PgPurchaseOrderHeaderRepository,
    PgPurchaseOrderItemRepository,
    PgServicePackageRepository,
)
from app.adapters.db.repositories.payments.wf_repo import PgWFPaymentRepository

__all__ = [
    "PgIngestionRunRepository",
    "PgSupplierBridgeRepository",
    "PgContractMasterRepository",
    "PgContractVersionRepository",
    "PgContractClauseRepository",
    "PgLPUItemRepository",
    "PgPurchaseOrderHeaderRepository",
    "PgPurchaseOrderItemRepository",
    "PgServicePackageRepository",
    "PgPurchaseOrderGcRepository",
    "PgCostCenterAccountRepository",
    "PgWFPaymentRepository",
    "PgRuleDefinitionRepository",
    "PgReconciliationRunRepository",
    "PgReconciliationFindingRepository",
    "PgAnalyticDetectorRepository",
    "PgAnalyticFindingRepository",
    "PgExtractionJobRepository",
]

"""Ports (Protocols) dos repositórios do domínio payments.

18 Protocols, 1:1 com as entidades de `app/core/domain/payments/`.
Implementações concretas em `app/adapters/db/repositories/payments/`.

Convenções:
  - Todos os métodos são `async` (asyncpg I/O).
  - Métodos retornam domain models (Pydantic) ou primitivos — nunca asyncpg.Record.
  - `get*` retornam `Entity | None` (None se não encontrado).
  - `list*` retornam `list[Entity]` (vazio se nada).
  - `bulk_insert(items)` retorna `int` (rows inserted).
  - `bulk_upsert(items)` retorna `int` (rows affected, insert+update).
  - Workflow (`mark_completed`, `update_status`) retorna `None` ou `bool`.
  - Catálogos (RuleDefinition, AnalyticDetector) usam `save` semântica upsert by `code`.

Organização do arquivo:
  1. Workflow / ingestion
  2. Catálogos (rules + detectors)
  3. Master data (suppliers, contracts, clauses)
  4. SAP entities
  5. LPU + WF (high volume)
  6. Findings
  7. Extraction
"""

from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID

from app.core.domain.payments import (
    AnalyticDetector,
    AnalyticFinding,
    ContractClause,
    ContractMaster,
    ContractVersion,
    CostCenterAccount,
    ExtractionJob,
    ExtractionStatus,
    FindingStatus,
    IngestionRun,
    LPUItem,
    PurchaseOrderGc,
    PurchaseOrderHeader,
    PurchaseOrderItem,
    ReconciliationFinding,
    ReconciliationRun,
    RuleDefinition,
    ServicePackage,
    SupplierBridge,
    WFPayment,
)

# ============================================================================
# 1. Workflow / ingestion
# ============================================================================


class IngestionRunRepository(Protocol):
    """Rastreabilidade de cargas de XLSX/TXT/PDF.

    Padrão de uso pelo loader:
        run = await repo.create(IngestionRun(source_type=..., target_table=..., ...))
        await repo.mark_running(run.id)
        try:
            # ... fazer a carga ...
            await repo.mark_completed(run.id, rows_inserted=N, rows_failed=0)
        except Exception as e:
            await repo.mark_failed(run.id, error_message=str(e))
    """

    async def create(self, run: IngestionRun) -> IngestionRun: ...
    async def get(self, run_id: UUID) -> IngestionRun | None: ...
    async def mark_running(self, run_id: UUID) -> None: ...
    async def mark_completed(
        self,
        run_id: UUID,
        *,
        rows_read: int,
        rows_inserted: int,
        rows_skipped: int = 0,
        rows_failed: int = 0,
    ) -> None: ...
    async def mark_failed(self, run_id: UUID, *, error_message: str) -> None: ...
    async def list_recent(self, *, limit: int = 50) -> list[IngestionRun]: ...


# ============================================================================
# 2. Catálogos (rules + detectors)
# ============================================================================


class RuleDefinitionRepository(Protocol):
    """Catálogo das 20 regras (R1-R6.9 + REGRA_LPU).

    `save` é upsert por `code` (idempotente). Permite re-rodar seed sem erro.
    """

    async def list_active(self) -> list[RuleDefinition]: ...
    async def list_all(self) -> list[RuleDefinition]: ...
    async def get(self, rule_id: UUID) -> RuleDefinition | None: ...
    async def get_by_code(self, code: str) -> RuleDefinition | None: ...
    async def save(self, rule: RuleDefinition) -> RuleDefinition: ...
    async def set_active(self, rule_id: UUID, active: bool) -> None: ...
    async def count(self) -> int: ...


class AnalyticDetectorRepository(Protocol):
    """Catálogo dos 11 detectores R7."""

    async def list_active(self) -> list[AnalyticDetector]: ...
    async def list_all(self) -> list[AnalyticDetector]: ...
    async def get(self, detector_id: UUID) -> AnalyticDetector | None: ...
    async def get_by_code(self, code: str) -> AnalyticDetector | None: ...
    async def save(self, detector: AnalyticDetector) -> AnalyticDetector: ...
    async def set_active(self, detector_id: UUID, active: bool) -> None: ...
    async def count(self) -> int: ...


# ============================================================================
# 3. Master data (suppliers + contracts + clauses)
# ============================================================================


class SupplierBridgeRepository(Protocol):
    """DE-PARA contrato SAP ↔ REF WS ↔ CNPJ. 147 rows iniciais.

    `bulk_upsert` é idempotente por (contrato_num_sap, ref_ws).
    """

    async def bulk_upsert(self, items: list[SupplierBridge]) -> int: ...
    async def get(self, supplier_id: UUID) -> SupplierBridge | None: ...
    async def get_by_contrato(self, contrato_num_sap: str) -> SupplierBridge | None: ...
    async def get_by_cnpj(self, cnpj: str) -> list[SupplierBridge]: ...
    async def list_all(self) -> list[SupplierBridge]: ...
    async def count(self) -> int: ...


class ContractMasterRepository(Protocol):
    """Contrato jurídico (cabeça). Versionado via ContractVersion."""

    async def create(self, master: ContractMaster) -> ContractMaster: ...
    async def get(self, master_id: UUID) -> ContractMaster | None: ...
    async def get_by_contrato(self, contrato_num_sap: str) -> ContractMaster | None: ...
    async def set_current_version(self, master_id: UUID, version_id: UUID) -> None: ...
    async def set_monitored(self, master_id: UUID, monitored: bool) -> None: ...
    async def list_monitored(self) -> list[ContractMaster]: ...
    async def list_all(self) -> list[ContractMaster]: ...


class ContractVersionRepository(Protocol):
    """Versão temporal de um contrato (aditivos)."""

    async def create(self, version: ContractVersion) -> ContractVersion: ...
    async def get(self, version_id: UUID) -> ContractVersion | None: ...
    async def get_current_for_master(
        self, master_id: UUID, *, at: date | None = None
    ) -> ContractVersion | None:
        """Retorna a versão vigente em `at` (default: hoje), ou None."""
        ...

    async def list_for_master(self, master_id: UUID) -> list[ContractVersion]: ...


class ContractClauseRepository(Protocol):
    """Cláusulas com embedding pgvector(1536). Fase 4 — similarity search."""

    async def bulk_insert(self, clauses: list[ContractClause]) -> int: ...
    async def get(self, clause_id: UUID) -> ContractClause | None: ...
    async def list_for_version(self, version_id: UUID) -> list[ContractClause]: ...
    async def search_by_embedding(
        self,
        embedding: list[float],
        *,
        contract_version_id: UUID | None = None,
        limit: int = 10,
    ) -> list[ContractClause]:
        """k-NN via ivfflat (vector_cosine_ops). Filtra por version se fornecida."""
        ...


# ============================================================================
# 4. SAP entities
# ============================================================================


class PurchaseOrderHeaderRepository(Protocol):
    """EKKO — 1.894 + 138 rows iniciais."""

    async def bulk_insert(self, items: list[PurchaseOrderHeader]) -> int: ...
    async def get_by_documento(
        self, documento_compras: str
    ) -> PurchaseOrderHeader | None: ...
    async def list_for_fornecedor(self, fornecedor: str) -> list[PurchaseOrderHeader]: ...
    async def count(self) -> int: ...


class PurchaseOrderItemRepository(Protocol):
    """EKPO — 25k + 44.7k rows iniciais."""

    async def bulk_insert(self, items: list[PurchaseOrderItem]) -> int: ...
    async def get(
        self, documento_compras: str, item: str
    ) -> PurchaseOrderItem | None: ...
    async def list_for_documento(
        self, documento_compras: str
    ) -> list[PurchaseOrderItem]: ...
    async def count(self) -> int: ...


class ServicePackageRepository(Protocol):
    """ESLL — 44.7k rows iniciais."""

    async def bulk_insert(self, items: list[ServicePackage]) -> int: ...
    async def get(self, pacote: str, linha: int) -> ServicePackage | None: ...
    async def list_for_servico(self, numero_servico: str) -> list[ServicePackage]: ...
    async def count(self) -> int: ...


class PurchaseOrderGcRepository(Protocol):
    """Sheet 'Contratos Guarda Chuvas' — 44.7k rows (R6.6-6.9)."""

    async def bulk_insert(self, items: list[PurchaseOrderGc]) -> int: ...
    async def get(
        self, documento_compras: str, item: str
    ) -> PurchaseOrderGc | None: ...
    async def list_for_servico(self, numero_servico: str) -> list[PurchaseOrderGc]: ...
    async def count(self) -> int: ...


class CostCenterAccountRepository(Protocol):
    """Sheet 'CC + CONTA' — 1.049 rows."""

    async def bulk_upsert(self, items: list[CostCenterAccount]) -> int: ...
    async def list_all(self) -> list[CostCenterAccount]: ...
    async def get_contas_for_cc(self, centro_de_custo: str) -> list[str]: ...
    async def count(self) -> int: ...


# ============================================================================
# 5. LPU + WF (high volume)
# ============================================================================


class LPUItemRepository(Protocol):
    """LPU — 3.1M rows iniciais (MSRV5). Particionada por ano em data_documento."""

    async def bulk_insert(self, items: list[LPUItem]) -> int: ...
    async def get(self, lpu_id: int) -> LPUItem | None: ...
    async def find_by_servico_e_data(
        self, numero_servico: str, at: date
    ) -> list[LPUItem]:
        """Retorna LPUItems do serviço cujo período inclui `at` (via JOIN com ContractVersion)."""
        ...

    async def count_by_year(self, year: int) -> int: ...
    async def count_total(self) -> int: ...


class WFPaymentRepository(Protocol):
    """Pagamento analítico WF1/WF2 — 869k rows iniciais. Particionada por trimestre."""

    async def bulk_insert(self, items: list[WFPayment]) -> int: ...
    async def get(self, wf_id: int, data_pedido: date) -> WFPayment | None:
        """PK composta (id, data_pedido) — partition key."""
        ...

    async def list_universe(
        self,
        *,
        since: date,
        until: date,
        empreiteira: str | None = None,
        limit: int = 1000,
    ) -> list[WFPayment]:
        """Filtro universal SDD §9 v1.1.1:
            status_os IN ('EXECUTADO','EM EXECUÇÃO')
            ∧ nivel_gerencial IN ('Em Pagamento','Medido')
            ∧ malogro ≠ 'ERROR'
        """
        ...

    async def count_universe(
        self, *, since: date, until: date, empreiteira: str | None = None
    ) -> int: ...
    async def count_total(self) -> int: ...


# ============================================================================
# 6. Findings
# ============================================================================


class ReconciliationRunRepository(Protocol):
    """1 execução do engine de regras."""

    async def create(self, run: ReconciliationRun) -> ReconciliationRun: ...
    async def get(self, run_id: UUID) -> ReconciliationRun | None: ...
    async def mark_completed(self, run_id: UUID, *, findings_created: int) -> None: ...
    async def mark_failed(self, run_id: UUID, *, error_message: str) -> None: ...
    async def list_recent(self, *, limit: int = 50) -> list[ReconciliationRun]: ...


class ReconciliationFindingRepository(Protocol):
    """Findings determinísticos/fuzzy (R1-R6.9, LPU)."""

    async def create(self, finding: ReconciliationFinding) -> ReconciliationFinding: ...
    async def bulk_insert(self, findings: list[ReconciliationFinding]) -> int: ...
    async def get(self, finding_id: UUID) -> ReconciliationFinding | None: ...
    async def update_status(
        self,
        finding_id: UUID,
        *,
        status: FindingStatus,
        analyst_id: UUID | None = None,
        decision_reason: str | None = None,
    ) -> None: ...
    async def list_inbox(
        self,
        *,
        status: FindingStatus = FindingStatus.OPEN,
        monitored_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReconciliationFinding]: ...
    async def count_open(self, *, monitored_only: bool = True) -> int: ...


class AnalyticFindingRepository(Protocol):
    """Findings estatísticos (R7)."""

    async def create(self, finding: AnalyticFinding) -> AnalyticFinding: ...
    async def bulk_insert(self, findings: list[AnalyticFinding]) -> int: ...
    async def get(self, finding_id: UUID) -> AnalyticFinding | None: ...
    async def update_status(
        self,
        finding_id: UUID,
        *,
        status: FindingStatus,
        analyst_id: UUID | None = None,
        decision_reason: str | None = None,
    ) -> None: ...
    async def list_inbox(
        self,
        *,
        status: FindingStatus = FindingStatus.OPEN,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AnalyticFinding]: ...
    async def count_open(self) -> int: ...


# ============================================================================
# 7. Extraction (Fase 4)
# ============================================================================


class ExtractionJobRepository(Protocol):
    """Jobs assíncronos de extração de PDF (alimentado por dramatiq worker)."""

    async def create(self, job: ExtractionJob) -> ExtractionJob: ...
    async def get(self, job_id: UUID) -> ExtractionJob | None: ...
    async def update_status(
        self,
        job_id: UUID,
        *,
        status: ExtractionStatus,
        error_message: str | None = None,
    ) -> None: ...
    async def set_results(
        self,
        job_id: UUID,
        *,
        extracted_fields: dict,
        confidence_per_field: dict,
        cost_brl,
        llm_model_used: str,
    ) -> None: ...
    async def list_pending(self, *, limit: int = 50) -> list[ExtractionJob]: ...
    async def list_for_review(self, *, limit: int = 50) -> list[ExtractionJob]: ...


__all__ = [
    "IngestionRunRepository",
    "RuleDefinitionRepository",
    "AnalyticDetectorRepository",
    "SupplierBridgeRepository",
    "ContractMasterRepository",
    "ContractVersionRepository",
    "ContractClauseRepository",
    "PurchaseOrderHeaderRepository",
    "PurchaseOrderItemRepository",
    "ServicePackageRepository",
    "PurchaseOrderGcRepository",
    "CostCenterAccountRepository",
    "LPUItemRepository",
    "WFPaymentRepository",
    "ReconciliationRunRepository",
    "ReconciliationFindingRepository",
    "AnalyticFindingRepository",
    "ExtractionJobRepository",
]

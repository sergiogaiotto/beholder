"""Enums e taxonomias controladas do domínio payments.

Todas as taxonomias controladas (status, severidade, técnicas) ficam aqui.
Centralizar evita string-typos espalhados em validators, repos e queries.

Mapeamento com CHECK constraints das migrations 001-006:
  - IngestionStatus  ↔ payments.ingestion_run.status
  - Severity         ↔ rule_definition.severity, analytic_detector.severity, *_finding.severity
  - FindingStatus    ↔ reconciliation_finding.status, analytic_finding.status
  - EngineType       ↔ rule_definition.engine_type
  - Technique        ↔ analytic_detector.technique
  - SourceType       ↔ lpu_item.source
  - ExtractionStatus ↔ extraction_job.status
  - RunStatus        ↔ reconciliation_run.status
  - TriggeredBy      ↔ reconciliation_run.triggered_by

WF taxonomias com valores conhecidos (Pré-B):
  - Sistema (2 vals)        — WF1/WF2
  - TipoDespesa (2 vals)    — CAPEX/OPEX

Não viram Enum (mantidos como str validado por whitelist no model):
  - status_os, nivel_gerencial, fase_atual, regional_soe_nova,
    categoria, tecnologia, atividade, objeto_do_contrato, tipo_de_lpu
  Razão: ou são taxonomias abertas/de crescimento, ou expostas só ao WFPayment.
"""

from __future__ import annotations

from enum import Enum


class IngestionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingStatus(str, Enum):
    OPEN = "open"
    IN_ANALYSIS = "in_analysis"
    ACCEPTED_FP = "accepted_fp"
    ESCALATED = "escalated"
    BLOCKED = "blocked"


class EngineType(str, Enum):
    SQL_DETERMINISTIC = "sql_deterministic"
    FUZZY = "fuzzy"
    MATH_TOLERANCE = "math_tolerance"
    # Deprecated v1.1.1 (R5.f passou a ser fuzzy). Mantido só p/ compat de seeds antigos.
    EMBEDDING = "embedding"


class Technique(str, Enum):
    ZSCORE = "zscore"
    IQR = "iqr"
    TIMESERIES_OUTLIER = "timeseries_outlier"
    CLUSTERING = "clustering"
    SQL_TEMPORAL = "sql_temporal"
    RATIO = "ratio"
    HEURISTIC = "heuristic"


class SourceType(str, Enum):
    MSRV5 = "msrv5"
    PDF = "pdf"
    MANUAL = "manual"
    XLSX = "xlsx"


class ExtractionStatus(str, Enum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    REVIEW = "review"
    APPROVED = "approved"
    FAILED = "failed"


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TriggeredBy(str, Enum):
    MANUAL = "manual"
    POST_INGESTION = "post_ingestion"
    SCHEDULED = "scheduled"


class Sistema(str, Enum):
    WF1 = "WF1"
    WF2 = "WF2"


class TipoDespesa(str, Enum):
    CAPEX = "CAPEX"
    OPEX = "OPEX"

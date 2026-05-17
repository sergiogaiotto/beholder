"""Sanity dos enums — bate com CHECK constraints das migrations 001-006.

Se uma migration mudar e o enum ficar para trás, esse teste falha cedo.
"""

from __future__ import annotations

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


def test_ingestion_status_values():
    assert {s.value for s in IngestionStatus} == {
        "pending", "running", "completed", "failed", "rolled_back",
    }


def test_severity_values():
    assert {s.value for s in Severity} == {"low", "medium", "high"}


def test_finding_status_values():
    assert {s.value for s in FindingStatus} == {
        "open", "in_analysis", "accepted_fp", "escalated", "blocked",
    }


def test_engine_type_values():
    # 'embedding' deprecated v1.1.1 mas mantido p/ compat
    assert {e.value for e in EngineType} == {
        "sql_deterministic", "fuzzy", "math_tolerance", "embedding",
    }


def test_technique_values():
    assert {t.value for t in Technique} == {
        "zscore", "iqr", "timeseries_outlier", "clustering",
        "sql_temporal", "ratio", "heuristic",
    }


def test_source_type_values():
    assert {s.value for s in SourceType} == {"msrv5", "pdf", "manual", "xlsx"}


def test_extraction_status_values():
    assert {s.value for s in ExtractionStatus} == {
        "pending", "extracting", "review", "approved", "failed",
    }


def test_run_status_values():
    assert {r.value for r in RunStatus} == {"running", "completed", "failed"}


def test_triggered_by_values():
    assert {t.value for t in TriggeredBy} == {
        "manual", "post_ingestion", "scheduled",
    }


def test_sistema_values():
    assert {s.value for s in Sistema} == {"WF1", "WF2"}


def test_tipo_despesa_values():
    assert {t.value for t in TipoDespesa} == {"CAPEX", "OPEX"}

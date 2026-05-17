"""Tests de AnalyticDetector e AnalyticFinding."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.domain.payments.analytics import AnalyticDetector, AnalyticFinding
from app.core.domain.payments.enums import FindingStatus, Severity, Technique


# ---------- AnalyticDetector ----------


def _det_kwargs(**overrides):
    base = dict(
        code="R7_LPU_OUTLIER",
        name="LPU outlier por serviço",
        description="Diferenças relevantes de custo/volume para a mesma LPU",
        technique="iqr",
        severity="medium",
        python_handler="app.core.services.payments.analytics.r7_lpu_outlier",
    )
    base.update(overrides)
    return base


def test_detector_happy():
    d = AnalyticDetector(**_det_kwargs())
    assert d.technique is Technique.IQR
    assert d.severity is Severity.MEDIUM
    assert d.version == 1


def test_detector_with_threshold():
    d = AnalyticDetector(**_det_kwargs(
        threshold_params={"iqr_factor": 1.5, "min_samples": 30},
    ))
    assert d.threshold_params["min_samples"] == 30


def test_detector_rejects_invalid_technique():
    with pytest.raises(ValidationError):
        AnalyticDetector(**_det_kwargs(technique="bayesian"))


def test_detector_all_techniques_supported():
    for t in ["zscore", "iqr", "timeseries_outlier", "clustering",
              "sql_temporal", "ratio", "heuristic"]:
        d = AnalyticDetector(**_det_kwargs(technique=t))
        assert d.technique.value == t


# ---------- AnalyticFinding ----------


def _finding_kwargs(**overrides):
    base = dict(
        detector_id=uuid4(),
        detector_code="R7_LPU_OUTLIER",
        severity="medium",
        score=2.5,
        expected_range={"min": 100, "max": 500, "method": "iqr"},
        actual_value={"value": 750.00},
    )
    base.update(overrides)
    return base


def test_finding_happy_individual():
    """Finding sobre 1 pagamento específico."""
    f = AnalyticFinding(**_finding_kwargs(
        wf_payment_id=12345,
        wf_payment_data_pedido=date(2025, 6, 1),
    ))
    assert f.status is FindingStatus.OPEN
    assert f.score == 2.5
    assert f.wf_payment_id == 12345


def test_finding_happy_aggregated():
    """Findings agregados (empreiteira fora do padrão) podem não ter wf_payment_id."""
    f = AnalyticFinding(**_finding_kwargs(
        supplier_id=uuid4(),
        evidence_payment_ids=[1, 2, 3, 4, 5],
    ))
    assert f.wf_payment_id is None
    assert len(f.evidence_payment_ids) == 5


def test_finding_accepts_negative_score():
    """z-score pode ser negativo — não rejeitar."""
    f = AnalyticFinding(**_finding_kwargs(score=-3.2))
    assert f.score == -3.2


def test_finding_requires_score():
    """score é DOUBLE PRECISION NOT NULL no DB."""
    kwargs = _finding_kwargs()
    del kwargs["score"]
    with pytest.raises(ValidationError, match="score"):
        AnalyticFinding(**kwargs)

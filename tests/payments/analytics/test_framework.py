"""Tests do framework analytics R7 (Fase 2.5 Bloco A).

Cobre:
  - Helpers stats puros: mean, stdev, zscore, quantile, iqr_bounds,
    is_outlier_iqr, is_outlier_zscore, decimal_places
  - AnalyticFindingDraft.to_finding() (conversão pro domain model)
  - ANALYTICS_REGISTRY + decorator register: idempotente, fail-fast em duplicate
  - universe_filter_for_detector: default, override, ignore flag
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from app.core.domain.payments import (
    AnalyticDetector,
    FindingStatus,
    Severity,
    Technique,
)
from app.core.services.payments.analytics import (
    ANALYTICS_REGISTRY,
    AnalyticContext,
    AnalyticFindingDraft,
    register,
    universe_filter_for_detector,
)
from app.core.services.payments.analytics._stats import (
    decimal_places,
    iqr_bounds,
    is_outlier_iqr,
    is_outlier_zscore,
    mean,
    quantile,
    stdev,
    zscore,
)


# ---------------------------------------------------------------------------
# Stats helpers — puros, sem DB
# ---------------------------------------------------------------------------


def test_mean_and_stdev_basics():
    assert mean([1.0, 2.0, 3.0]) == 2.0
    assert mean([5.0]) == 5.0
    with pytest.raises(ValueError):
        mean([])
    # sample stdev (N-1): conhecido [1,2,3,4,5] → sd≈1.5811
    assert stdev([1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(1.5811, abs=1e-4)
    # N<2 → 0.
    assert stdev([42.0]) == 0.0
    assert stdev([]) == 0.0


def test_zscore_basics():
    # Distribuição [1..5]: media 3, sd ≈ 1.58.
    assert zscore(3.0, [1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(0.0)
    # Valor 5: z = (5-3)/1.58 ≈ 1.265.
    assert zscore(5.0, [1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(1.2649, abs=1e-4)
    # Constante: stdev=0 → z=0 (convenção, evita NaN).
    assert zscore(10.0, [5.0, 5.0, 5.0]) == 0.0


def test_quantile_validates_range_and_returns_median():
    with pytest.raises(ValueError):
        quantile([1.0, 2.0], -0.1)
    with pytest.raises(ValueError):
        quantile([1.0, 2.0], 1.1)
    # Quantile 0.5 de [1..5] = 3 (mediana).
    assert quantile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0
    # Quantile 0.0 = mínimo.
    assert quantile([5.0, 1.0, 3.0], 0.0) == 1.0
    # Quantile 1.0 = máximo.
    assert quantile([5.0, 1.0, 3.0], 1.0) == 5.0


def test_iqr_bounds_classical_tukey():
    # [1..9]: Q1=3, Q3=7, IQR=4. Lower=3-6=-3, Upper=7+6=13.
    q1, q3, lo, hi = iqr_bounds([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
    assert q1 == pytest.approx(3.0)
    assert q3 == pytest.approx(7.0)
    assert lo == pytest.approx(-3.0)
    assert hi == pytest.approx(13.0)
    # Factor=3 alarga bounds.
    _q1, _q3, lo3, hi3 = iqr_bounds([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0], factor=3.0)
    assert lo3 < lo
    assert hi3 > hi


def test_is_outlier_iqr():
    values = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0]
    assert is_outlier_iqr(13.5, values) is False  # dentro do Q1-Q3
    assert is_outlier_iqr(100.0, values) is True  # bem fora upper fence
    # Amostras muito pequenas (<4) viram no-op (False).
    assert is_outlier_iqr(1000.0, [5.0, 5.0]) is False


def test_is_outlier_zscore():
    values = [10.0, 11.0, 12.0, 11.0, 10.0, 11.0, 12.0]
    assert is_outlier_zscore(11.0, values) is False
    assert is_outlier_zscore(100.0, values) is True  # |z| >> 2.0
    # threshold customizável.
    assert is_outlier_zscore(11.5, values, threshold=0.5) is True


def test_decimal_places_counts_non_zero_decimals():
    assert decimal_places(1.0) == 0
    assert decimal_places(1.5) == 1
    assert decimal_places(1.50) == 1   # trailing zero ignorado
    assert decimal_places(1.501) == 3
    assert decimal_places(0.0) == 0
    # Notação científica → 99 (flag explícita).
    assert decimal_places(1e-10) == 99


# ---------------------------------------------------------------------------
# AnalyticFindingDraft.to_finding
# ---------------------------------------------------------------------------


def test_draft_to_finding_basic_conversion():
    draft = AnalyticFindingDraft(
        detector_code="R7_LPU_OUTLIER",
        severity=Severity.MEDIUM,
        score=3.5,
        expected_range={"min": 10.0, "max": 100.0, "method": "iqr"},
        actual_value={"preco_unitario": 250.0},
        wf_payment_id=12345,
        evidence_payment_ids=[100, 200, 300],
        reason="acima do Q3 + 1.5 IQR",
    )
    detector_id = uuid4()
    finding = draft.to_finding(detector_id=detector_id)

    assert finding.detector_id == detector_id
    assert finding.detector_code == "R7_LPU_OUTLIER"
    assert finding.severity == Severity.MEDIUM
    assert finding.score == 3.5
    assert finding.expected_range == {"min": 10.0, "max": 100.0, "method": "iqr"}
    assert finding.actual_value == {"preco_unitario": 250.0}
    assert finding.wf_payment_id == 12345
    assert finding.evidence_payment_ids == [100, 200, 300]
    assert finding.status == FindingStatus.OPEN
    # `reason` é informativo — NÃO vai pro domain model.
    assert not hasattr(finding, "reason")


def test_draft_to_finding_minimal_fields_for_aggregated_detector():
    """Detectores agregados (clustering, períodos) podem não ter wf_payment_id."""
    draft = AnalyticFindingDraft(
        detector_code="R7_EMPREITEIRA_OUT_PADRAO",
        severity=Severity.MEDIUM,
        score=0.85,
        expected_range={"isolation_threshold": 0.7},
        actual_value={"empreiteira": "X CONSTRUTORA"},
        supplier_id=uuid4(),
    )
    finding = draft.to_finding(detector_id=uuid4())
    assert finding.wf_payment_id is None
    assert finding.evidence_payment_ids == []


# ---------------------------------------------------------------------------
# Registry pattern
# ---------------------------------------------------------------------------


def test_register_decorator_adds_to_registry():
    """register() popula ANALYTICS_REGISTRY pela chave do code."""
    code = f"R7_TEST_REG_{uuid4().hex[:6]}"

    async def _stub_handler(ctx):  # noqa: D401
        yield AnalyticFindingDraft(
            detector_code=code,
            severity=Severity.LOW,
            score=0.0,
            expected_range={},
            actual_value={},
        )

    decorated = register(code)(_stub_handler)
    assert ANALYTICS_REGISTRY[code] is decorated
    # Cleanup
    del ANALYTICS_REGISTRY[code]


def test_register_duplicate_code_fails_fast():
    code = f"R7_TEST_DUP_{uuid4().hex[:6]}"

    async def _h(ctx):
        yield AnalyticFindingDraft(
            detector_code=code, severity=Severity.LOW, score=0.0,
            expected_range={}, actual_value={},
        )

    register(code)(_h)
    with pytest.raises(ValueError, match="já registrado"):
        register(code)(_h)
    # Cleanup
    del ANALYTICS_REGISTRY[code]


# ---------------------------------------------------------------------------
# universe_filter_for_detector
# ---------------------------------------------------------------------------


def _make_detector(threshold: dict | None = None) -> AnalyticDetector:
    return AnalyticDetector(
        code="R7_TEST",
        name="t",
        description="t",
        technique=Technique.IQR,
        severity=Severity.LOW,
        threshold_params=threshold or {},
        python_handler="x",
    )


def test_universe_filter_returns_default_when_no_override():
    d = _make_detector({})
    filt = universe_filter_for_detector(d)
    assert "status_os IN ('EXECUTADO', 'EM EXECUÇÃO')" in filt
    assert "nivel_gerencial IN ('Em Pagamento', 'Medido')" in filt
    assert "malogro <> 'ERROR'" in filt


def test_universe_filter_respects_override_string():
    d = _make_detector({"universe_filter": "data_pedido > '2025-01-01'"})
    assert universe_filter_for_detector(d) == "data_pedido > '2025-01-01'"


def test_universe_filter_ignore_flag_returns_empty():
    """Detectores como R7_VALIDADE_VENCIDA querem olhar pagamentos FORA do
    universo operacional ativo de propósito."""
    d = _make_detector({"ignore_universe_filter": True})
    assert universe_filter_for_detector(d) == ""

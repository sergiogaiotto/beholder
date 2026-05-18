"""Integration tests dos 2 repos analytics R7."""

from __future__ import annotations

from datetime import date

from app.adapters.db.repositories.payments import (
    PgAnalyticDetectorRepository,
    PgAnalyticFindingRepository,
)
from app.core.domain.payments import (
    AnalyticDetector,
    AnalyticFinding,
    FindingStatus,
    Severity,
    Technique,
)


# ---------- AnalyticDetector ----------


async def test_detector_seed_populated():
    """Migration 007 popula 11 detectores R7."""
    repo = PgAnalyticDetectorRepository()
    active = await repo.list_active()
    assert len(active) == 11
    codes = {d.code for d in active}
    assert "R7_LPU_OUTLIER" in codes
    assert "R7_VALIDADE_VENCIDA" in codes


async def test_detector_save_upsert():
    repo = PgAnalyticDetectorRepository()
    det = AnalyticDetector(
        code="R7_TEST",
        name="test",
        description="d",
        technique=Technique.HEURISTIC,
        severity=Severity.LOW,
        python_handler="x.y",
    )
    saved = await repo.save(det)
    assert saved.code == "R7_TEST"

    det.severity = Severity.HIGH
    updated = await repo.save(det)
    assert updated.severity is Severity.HIGH

    by_code = await repo.get_by_code("R7_TEST")
    assert by_code.severity is Severity.HIGH


# ---------- AnalyticFinding ----------


async def test_finding_create_individual_e_aggregated(test_user_id):
    detectors_repo = PgAnalyticDetectorRepository()
    f_repo = PgAnalyticFindingRepository()
    det = await detectors_repo.get_by_code("R7_LPU_OUTLIER")

    # Finding individual (sobre 1 pagamento)
    f_individual = AnalyticFinding(
        detector_id=det.id,
        detector_code=det.code,
        severity=det.severity,
        wf_payment_id=42,
        wf_payment_data_pedido=date(2025, 6, 1),
        score=2.5,
        expected_range={"min": 100, "max": 500},
        actual_value={"value": 750},
    )
    await f_repo.create(f_individual)

    # Finding agregado (empreiteira fora do padrão — sem wf_payment_id)
    f_agg = AnalyticFinding(
        detector_id=det.id,
        detector_code=det.code,
        severity=det.severity,
        score=-1.2,
        expected_range={"min": -1.0, "max": 1.0},
        actual_value={"value": -1.2},
        evidence_payment_ids=[10, 20, 30],
    )
    await f_repo.create(f_agg)

    fetched_i = await f_repo.get(f_individual.id)
    assert fetched_i.score == 2.5
    assert fetched_i.wf_payment_id == 42

    fetched_a = await f_repo.get(f_agg.id)
    assert fetched_a.wf_payment_id is None
    assert fetched_a.evidence_payment_ids == [10, 20, 30]


async def test_finding_bulk_insert_e_inbox(test_user_id):
    detectors_repo = PgAnalyticDetectorRepository()
    f_repo = PgAnalyticFindingRepository()
    det = await detectors_repo.get_by_code("R7_LPU_OUTLIER")

    findings = [
        AnalyticFinding(
            detector_id=det.id,
            detector_code=det.code,
            severity=det.severity,
            score=float(i),
            expected_range={"min": 0, "max": 1},
            actual_value={"v": i},
        )
        for i in range(3)
    ]
    n = await f_repo.bulk_insert(findings)
    assert n == 3
    assert await f_repo.count_open() == 3

    inbox = await f_repo.list_inbox()
    assert len(inbox) == 3


async def test_finding_update_status(test_user_id):
    detectors_repo = PgAnalyticDetectorRepository()
    f_repo = PgAnalyticFindingRepository()
    det = await detectors_repo.get_by_code("R7_LPU_OUTLIER")

    f = AnalyticFinding(
        detector_id=det.id,
        detector_code=det.code,
        severity=det.severity,
        score=2.0,
        expected_range={"min": 0, "max": 1},
        actual_value={"v": 2},
    )
    await f_repo.create(f)

    await f_repo.update_status(
        f.id,
        status=FindingStatus.ESCALATED,
        analyst_id=test_user_id,
        decision_reason="Encaminhado p/ jurídico",
    )
    fetched = await f_repo.get(f.id)
    assert fetched.status is FindingStatus.ESCALATED
    assert fetched.analyst_id == test_user_id

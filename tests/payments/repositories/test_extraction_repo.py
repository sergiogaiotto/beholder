"""Integration tests do PgExtractionJobRepository (Fase 4 stub)."""

from __future__ import annotations

from decimal import Decimal

from app.adapters.db.repositories.payments import PgExtractionJobRepository
from app.core.domain.payments import ExtractionJob, ExtractionStatus


async def test_create_then_get(test_user_id):
    repo = PgExtractionJobRepository()
    job = ExtractionJob(
        pdf_storage_key="contracts/2025/abc.pdf",
        pdf_filename="contract.pdf",
        pdf_size_bytes=1_234_567,
        status=ExtractionStatus.PENDING,
        uploaded_by_id=test_user_id,
    )
    await repo.create(job)

    fetched = await repo.get(job.id)
    assert fetched is not None
    assert fetched.status is ExtractionStatus.PENDING
    assert fetched.uploaded_by_id == test_user_id


async def test_update_status_e_set_results(test_user_id):
    repo = PgExtractionJobRepository()
    job = ExtractionJob(
        pdf_storage_key="contracts/2025/xyz.pdf",
        pdf_filename="contract.pdf",
        pdf_size_bytes=5000,
        status=ExtractionStatus.PENDING,
        uploaded_by_id=test_user_id,
    )
    await repo.create(job)

    await repo.update_status(job.id, status=ExtractionStatus.EXTRACTING)
    r = await repo.get(job.id)
    assert r.status is ExtractionStatus.EXTRACTING

    await repo.set_results(
        job.id,
        extracted_fields={"val_fix_cab": 1500000.0, "objeto": "Manutenção"},
        confidence_per_field={"val_fix_cab": 0.95, "objeto": 0.78},
        cost_brl=Decimal("0.37"),
        llm_model_used="sabia-4",
    )
    r = await repo.get(job.id)
    assert r.status is ExtractionStatus.REVIEW
    assert r.llm_model_used == "sabia-4"
    assert r.cost_brl == Decimal("0.37")
    assert r.extraction_finished_at is not None


async def test_list_pending_e_for_review(test_user_id):
    repo = PgExtractionJobRepository()

    j_pending = ExtractionJob(
        pdf_storage_key="p/1.pdf",
        pdf_filename="1.pdf",
        pdf_size_bytes=100,
        status=ExtractionStatus.PENDING,
        uploaded_by_id=test_user_id,
    )
    j_review = ExtractionJob(
        pdf_storage_key="p/2.pdf",
        pdf_filename="2.pdf",
        pdf_size_bytes=200,
        status=ExtractionStatus.REVIEW,
        uploaded_by_id=test_user_id,
    )
    await repo.create(j_pending)
    await repo.create(j_review)

    pending = await repo.list_pending()
    assert [j.id for j in pending] == [j_pending.id]

    review = await repo.list_for_review()
    assert [j.id for j in review] == [j_review.id]


async def test_update_status_with_error_message(test_user_id):
    repo = PgExtractionJobRepository()
    job = ExtractionJob(
        pdf_storage_key="p/3.pdf",
        pdf_filename="3.pdf",
        pdf_size_bytes=300,
        status=ExtractionStatus.EXTRACTING,
        uploaded_by_id=test_user_id,
    )
    await repo.create(job)

    await repo.update_status(
        job.id,
        status=ExtractionStatus.FAILED,
        error_message="OCR failed: empty pages",
    )
    fetched = await repo.get(job.id)
    assert fetched.status is ExtractionStatus.FAILED
    assert fetched.error_message == "OCR failed: empty pages"

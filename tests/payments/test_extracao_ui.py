"""Tests da Fase 4 — PDF Extraction (Bloco A: backend + tela lista).

Cobre:
  - Schemas Pydantic ExtractedContractFields/ExtractedLPUItem
  - MockExtractionClient (cliente injetável determinístico)
  - PaymentsExtractionService:
      · queue_upload: cria ExtractionJob + salva no DocStore + despacha actor
      · process: pipeline storage → text → LLM → set_results
      · list_recent_jobs / get_job_detail (serialização pra UI)
  - Rotas HTTP:
      · GET  /contratos/extracao (render)
      · POST /contratos/extracao/upload (303 PRG + actor disparado)
      · GET  /contratos/extracao/jobs (partial HTMX)
      · matriz de roles consistente com Fase 3

Sem chamadas reais a Maritaca/docling — todos os tests usam MockExtractionClient.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters.db.postgres import init_db
from app.adapters.db.postgres_payments import init_payments_schema
from app.adapters.db.repositories.payments.extraction_repo import (
    PgExtractionJobRepository,
)
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.domain.payments import ExtractionStatus
from app.core.services.auth_service import AuthService
from app.core.services.payments.extraction import (
    ExtractedContractFields,
    ExtractedLPUItem,
    MockExtractionClient,
)
from app.core.services.payments.extraction.service import PaymentsExtractionService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_user(username: str, password: str, roles: list[str]) -> UUID:
    await init_db()
    auth = AuthService(PgUserRepository())
    u = await auth.register(username=username, password=password, roles=roles)
    return u.id


async def _login_as(client: AsyncClient, username: str, password: str) -> None:
    resp = await client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def _make_service(tmp_path: Path, fields: ExtractedContractFields | None = None):
    from app.adapters.storage.filesystem_document_store import (
        FilesystemDocumentStore,
    )
    store = FilesystemDocumentStore(root=tmp_path)
    client = MockExtractionClient(result_fields=fields)
    return PaymentsExtractionService(document_store=store, llm_client=client)


def _fake_pdf_bytes() -> bytes:
    """Bytes que não precisam ser PDF válido — o pipeline real usa
    pdfplumber, mas testamos `process` substituindo o cliente LLM
    pelo mock; `_pdf_to_text` é tolerante (devolve string vazia em erro).
    Para tests do `process` end-to-end usamos um payload arbitrário que
    o mock client ignora."""
    return b"%PDF-1.4 fake-pdf-bytes-for-test"


@pytest.fixture
async def _schema():
    await init_db()
    await init_payments_schema()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def test_extracted_contract_fields_empty_defaults():
    f = ExtractedContractFields()
    assert f.uf == []
    assert f.cidade == []
    assert f.lpu_items == []
    assert f.empreiteira_cnpj is None


def test_extracted_contract_fields_confidence_per_field():
    f = ExtractedContractFields(
        empreiteira_nome="X", empreiteira_cnpj="12345678000199",
        uf=["SP"], lpu_items=[
            ExtractedLPUItem(numero_servico="S1", descricao="d", preco_unitario=Decimal("10")),
        ],
    )
    conf = f.confidence_per_field()
    assert conf["empreiteira_nome"] == 1.0
    assert conf["empreiteira_cnpj"] == 1.0
    assert conf["uf"] == 1.0
    assert conf["lpu_items"] == 1.0
    # campos vazios → 0.0
    assert conf["categoria"] == 0.0
    assert conf["val_fix_cab"] == 0.0


# ---------------------------------------------------------------------------
# MockExtractionClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_client_default_payload():
    client = MockExtractionClient()
    res = await client.extract(pdf_text="fake", pdf_filename="x.pdf")
    assert res.fields.empreiteira_nome == "MOCK EMPREITEIRA LTDA"
    assert res.cost_brl == Decimal("0.37")
    assert res.llm_model_used == "mock-sabia-4"


@pytest.mark.asyncio
async def test_mock_client_custom_fields():
    fields = ExtractedContractFields(empreiteira_nome="CUSTOM SA")
    client = MockExtractionClient(result_fields=fields, cost_brl=Decimal("1.50"))
    res = await client.extract(pdf_text="x", pdf_filename="y.pdf")
    assert res.fields.empreiteira_nome == "CUSTOM SA"
    assert res.cost_brl == Decimal("1.50")


# ---------------------------------------------------------------------------
# Service — queue_upload + process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_upload_persists_pending_job_and_returns_id(
    _schema, tmp_path, monkeypatch
):
    from app.workers import payments_extraction
    sent: list[dict] = []
    monkeypatch.setattr(
        payments_extraction.extract_pdf, "send",
        lambda *a, **kw: sent.append(kw) or type("Msg", (), {"message_id": "x"})(),
    )

    user_id = await _create_user("ext_q", "senha-123", roles=["controladoria"])
    svc = _make_service(tmp_path)
    job_id = await svc.queue_upload(
        pdf_bytes=_fake_pdf_bytes(),
        filename="contrato.pdf",
        uploaded_by_id=user_id,
    )
    assert isinstance(job_id, UUID)

    # Job persistido.
    repo = PgExtractionJobRepository()
    job = await repo.get(job_id)
    assert job is not None
    assert job.status == ExtractionStatus.PENDING
    assert job.pdf_filename == "contrato.pdf"
    assert job.pdf_storage_key.startswith(f"payments/contracts/{job_id}/")
    assert job.uploaded_by_id == user_id

    # Actor disparado uma vez com o job_id.
    assert len(sent) == 1
    assert sent[0]["job_id"] == str(job_id)


@pytest.mark.asyncio
async def test_queue_upload_rejects_empty_bytes(_schema, tmp_path):
    svc = _make_service(tmp_path)
    with pytest.raises(ValueError, match="vazio"):
        await svc.queue_upload(
            pdf_bytes=b"", filename="a.pdf", uploaded_by_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_queue_upload_rejects_non_pdf_filename(_schema, tmp_path):
    svc = _make_service(tmp_path)
    with pytest.raises(ValueError, match=".pdf"):
        await svc.queue_upload(
            pdf_bytes=_fake_pdf_bytes(), filename="contrato.xlsx",
            uploaded_by_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_process_persists_results_when_llm_succeeds(
    _schema, tmp_path, monkeypatch
):
    """End-to-end de process(): salva PDF → text vazio (pdfplumber falha
    em bytes fake) → DEVERIA marcar como failed. Validamos esse caminho
    primeiro pra garantir o error path."""
    from app.workers import payments_extraction
    monkeypatch.setattr(
        payments_extraction.extract_pdf, "send",
        lambda *a, **kw: type("Msg", (), {"message_id": "x"})(),
    )

    user_id = await _create_user("ext_p", "senha-123", roles=["controladoria"])
    svc = _make_service(tmp_path)
    job_id = await svc.queue_upload(
        pdf_bytes=_fake_pdf_bytes(), filename="x.pdf", uploaded_by_id=user_id,
    )
    await svc.process(job_id)

    repo = PgExtractionJobRepository()
    job = await repo.get(job_id)
    # Como bytes fake não viram texto, pipeline marca failed.
    assert job.status == ExtractionStatus.FAILED
    assert job.error_message and "vazio" in job.error_message.lower()


@pytest.mark.asyncio
async def test_process_bypasses_pdf_when_text_available(
    _schema, tmp_path, monkeypatch
):
    """Versão `success path` que monkeypatcha `_pdf_to_text` pra devolver
    texto não-vazio. Mock client retorna payload preenchido → set_results
    persiste em status='review'."""
    from app.core.services.payments.extraction import service as svc_mod
    from app.workers import payments_extraction
    monkeypatch.setattr(
        payments_extraction.extract_pdf, "send",
        lambda *a, **kw: type("Msg", (), {"message_id": "x"})(),
    )
    monkeypatch.setattr(svc_mod, "_pdf_to_text", lambda b: "TEXTO EXTRAIDO")

    user_id = await _create_user("ext_ok", "senha-123", roles=["controladoria"])
    fields = ExtractedContractFields(
        empreiteira_nome="ACME LTDA",
        empreiteira_cnpj="11222333000144",
        categoria="FIXO MENSAL",
        val_fix_cab=Decimal("15000.00"),
    )
    svc = _make_service(tmp_path, fields=fields)
    job_id = await svc.queue_upload(
        pdf_bytes=_fake_pdf_bytes(), filename="acme.pdf",
        uploaded_by_id=user_id,
    )
    await svc.process(job_id)

    detail = await svc.get_job_detail(job_id)
    assert detail["status"] == "review"
    assert detail["extracted_fields"]["empreiteira_nome"] == "ACME LTDA"
    assert detail["extracted_fields"]["categoria"] == "FIXO MENSAL"
    assert detail["confidence_per_field"]["empreiteira_nome"] == 1.0
    assert detail["llm_model_used"] == "mock-sabia-4"
    assert detail["cost_brl"] == 0.37


# ---------------------------------------------------------------------------
# Service — listings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_recent_jobs_empty(_schema, tmp_path):
    svc = _make_service(tmp_path)
    jobs = await svc.list_recent_jobs()
    assert jobs == []


@pytest.mark.asyncio
async def test_list_recent_jobs_returns_formatted(_schema, tmp_path, monkeypatch):
    from app.workers import payments_extraction
    monkeypatch.setattr(
        payments_extraction.extract_pdf, "send",
        lambda *a, **kw: type("Msg", (), {"message_id": "x"})(),
    )

    user_id = await _create_user("ext_l", "senha-123", roles=["controladoria"])
    svc = _make_service(tmp_path)
    await svc.queue_upload(
        pdf_bytes=_fake_pdf_bytes(), filename="a.pdf", uploaded_by_id=user_id,
    )

    jobs = await svc.list_recent_jobs()
    assert len(jobs) == 1
    j = jobs[0]
    assert j["pdf_filename"] == "a.pdf"
    assert j["status_label"] == "Aguardando"  # status=pending
    assert j["cost_brl_fmt"].startswith("R$")
    assert "/" in j["created_at_fmt"]
    assert j["uploaded_by_username"] == "ext_l"


@pytest.mark.asyncio
async def test_get_job_detail_returns_none_for_unknown(_schema, tmp_path):
    svc = _make_service(tmp_path)
    detail = await svc.get_job_detail(UUID("00000000-0000-0000-0000-000000000000"))
    assert detail is None


# ---------------------------------------------------------------------------
# Rotas HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extracao_page_renders_with_empty_state():
    from app.main import app

    await _create_user("ext_pg", "senha-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ext_pg", "senha-123")
        resp = await client.get("/payments/empreiteiras-wf/contratos/extracao")
    assert resp.status_code == 200
    body = resp.text
    assert "Extração de PDFs de Contrato" in body
    assert "nenhuma extração ainda" in body


@pytest.mark.asyncio
async def test_extracao_route_requires_role():
    from app.main import app

    await _create_user("n3_ext", "senha-123", roles=["analista_n3"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Anônimo → redirect
        resp = await client.get(
            "/payments/empreiteiras-wf/contratos/extracao", follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        # analista_n3 → 403
        await _login_as(client, "n3_ext", "senha-123")
        resp = await client.get("/payments/empreiteiras-wf/contratos/extracao")
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_extracao_upload_route_303_with_job_id(monkeypatch, _schema):
    from app.main import app
    from app.workers import payments_extraction

    sent: dict = {}

    def _fake_send(*a, **kw):
        sent["kwargs"] = kw
        return type("Msg", (), {"message_id": "x"})()

    monkeypatch.setattr(payments_extraction.extract_pdf, "send", _fake_send)

    await _create_user("ext_up", "senha-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ext_up", "senha-123")
        resp = await client.post(
            "/payments/empreiteiras-wf/contratos/extracao/upload",
            files={"file": ("contrato.pdf", _fake_pdf_bytes(), "application/pdf")},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "just_uploaded=" in resp.headers["location"]
    assert sent["kwargs"]["job_id"]


@pytest.mark.asyncio
async def test_extracao_upload_rejects_empty():
    from app.main import app

    await _create_user("ext_em", "senha-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ext_em", "senha-123")
        resp = await client.post(
            "/payments/empreiteiras-wf/contratos/extracao/upload",
            files={"file": ("c.pdf", b"", "application/pdf")},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_extracao_jobs_partial_renders_table_only(monkeypatch, _schema):
    from app.main import app
    from app.workers import payments_extraction

    monkeypatch.setattr(
        payments_extraction.extract_pdf, "send",
        lambda *a, **kw: type("Msg", (), {"message_id": "x"})(),
    )

    user_id = await _create_user("ext_pp", "senha-123", roles=["controladoria"])
    from app.adapters.storage.filesystem_document_store import FilesystemDocumentStore
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = FilesystemDocumentStore(root=tmp)
        svc = PaymentsExtractionService(document_store=store, llm_client=MockExtractionClient())
        await svc.queue_upload(
            pdf_bytes=_fake_pdf_bytes(), filename="seed.pdf",
            uploaded_by_id=user_id,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _login_as(client, "ext_pp", "senha-123")
            resp = await client.get(
                "/payments/empreiteiras-wf/contratos/extracao/jobs"
            )
    assert resp.status_code == 200
    body = resp.text
    assert 'id="extracao-jobs"' in body
    assert "seed.pdf" in body
    # Partial não traz o shell completo.
    assert "Extração de PDFs de Contrato" not in body


@pytest.mark.asyncio
async def test_nav_left_shows_extracao_entry_under_pagamentos():
    """Sub-entry 'Extração PDF' aparece no grupo Pagamentos."""
    from app.main import app

    await _create_user("ext_nav", "senha-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ext_nav", "senha-123")
        resp = await client.get("/")
    body = resp.text
    assert "Extração PDF" in body
    assert 'href="/payments/empreiteiras-wf/contratos/extracao"' in body

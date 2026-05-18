"""Tests da Fase 3.5 — Ingestão XLSX/MSRV5 via UI.

Cobre:
  - PaymentsIngestionService.list_projections (catálogo)
  - queue_upload: salva no DocumentStore + cria IngestionRun(PENDING) +
    despacha actor (via StubBroker em testes)
  - list_recent_runs / get_run: serialização para a tabela da UI
  - Loader refactor: existing_run_id reusa run pré-criado
  - Rotas HTTP:
      · GET  /payments/empreiteiras-wf/ingestao (200 + cards + tabela)
      · POST /payments/empreiteiras-wf/ingestao/upload (303 + banner PRG)
      · GET  /.../ingestao/runs (partial HTMX)
      · GET  /.../ingestao/runs/{run_id} (JSON status)
      · roles + anônimo seguem a matriz da Fase 3
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters.db.postgres import init_db
from app.adapters.db.postgres_payments import connect_payments, init_payments_schema
from app.adapters.db.repositories.payments import PgIngestionRunRepository
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.domain.payments import IngestionStatus
from app.core.services.auth_service import AuthService
from app.core.services.payments.ingestion_service import (
    PROJECTION_CATALOG,
    PaymentsIngestionService,
    get_projection_info,
)


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


def _fake_xlsx_bytes() -> bytes:
    """Conteúdo binário arbitrário — não precisa ser XLSX válido pra testar
    a fila (parser só roda dentro do actor, que stubBroker não dispara em
    testes)."""
    return b"PK\x03\x04fake-xlsx-content-not-actually-parseable"


# Fixture leve para garantir que o schema payments está criado.
@pytest.fixture
async def _payments_schema():
    await init_db()
    await init_payments_schema()


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------


def test_projection_catalog_has_eight_entries():
    assert len(PROJECTION_CATALOG) == 8
    keys = {p.key for p in PROJECTION_CATALOG}
    assert keys == {
        "supplier_bridge", "wf_payment", "msrv5",
        "ekko", "ekpo", "esll", "gc", "cost_center",
    }


def test_projection_info_lookup_known_unknown():
    assert get_projection_info("wf_payment").source_type == "analitico_wf"
    assert get_projection_info("msrv5").accept == ".txt"
    assert get_projection_info("desconhecida") is None


def test_list_projections_serializes_for_ui():
    svc = PaymentsIngestionService()
    proj = svc.list_projections()
    assert len(proj) == 8
    # Cada item tem os campos consumidos pelo template (card).
    expected = {"key", "label", "description", "accept", "source_type"}
    for p in proj:
        assert expected.issubset(p.keys())


# ---------------------------------------------------------------------------
# queue_upload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_upload_creates_pending_run_and_returns_id(
    _payments_schema, tmp_path
):
    """Caminho feliz: queue_upload persiste o IngestionRun em PENDING +
    salva no DocumentStore + despacha actor (StubBroker captura).
    """
    # Usa FilesystemDocumentStore com tmp_path pra isolar o filesystem.
    from app.adapters.storage.filesystem_document_store import (
        FilesystemDocumentStore,
    )
    store = FilesystemDocumentStore(root=tmp_path)
    user_id = await _create_user("queue_upload", "senha-123", roles=["controladoria"])

    svc = PaymentsIngestionService(document_store=store)
    run_id = await svc.queue_upload(
        file_bytes=_fake_xlsx_bytes(),
        filename="contratos.xlsx",
        projection_name="supplier_bridge",
        user_id=user_id,
    )
    assert isinstance(run_id, UUID)

    # Confere o run no DB.
    repo = PgIngestionRunRepository()
    run = await repo.get(run_id)
    assert run is not None
    assert run.status == IngestionStatus.PENDING
    assert run.source_type == "xlsx"
    assert run.source_filename == "contratos.xlsx"
    assert run.source_size_bytes == len(_fake_xlsx_bytes())
    assert run.source_sha256 is not None and len(run.source_sha256) == 64
    assert run.target_table.startswith("payments.")
    assert run.metadata.get("projection_name") == "supplier_bridge"
    assert run.metadata.get("via") == "ui_upload"

    # Confere o arquivo no storage (chave inclui o run_id).
    storage_key = f"payments/ingestion/{run_id}/contratos.xlsx"
    assert await store.exists(storage_key)
    data = await store.get(storage_key)
    assert data == _fake_xlsx_bytes()


@pytest.mark.asyncio
async def test_queue_upload_rejects_unknown_projection(_payments_schema):
    svc = PaymentsIngestionService()
    with pytest.raises(ValueError, match="projection desconhecida"):
        await svc.queue_upload(
            file_bytes=_fake_xlsx_bytes(),
            filename="x.xlsx",
            projection_name="inexistente",
            user_id=None,
        )


@pytest.mark.asyncio
async def test_queue_upload_rejects_empty_file(_payments_schema):
    svc = PaymentsIngestionService()
    with pytest.raises(ValueError, match="vazio"):
        await svc.queue_upload(
            file_bytes=b"",
            filename="x.xlsx",
            projection_name="supplier_bridge",
            user_id=None,
        )


# ---------------------------------------------------------------------------
# list_recent_runs / get_run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_recent_runs_serializes_for_table(_payments_schema, tmp_path):
    from app.adapters.storage.filesystem_document_store import (
        FilesystemDocumentStore,
    )
    store = FilesystemDocumentStore(root=tmp_path)
    user_id = await _create_user("list_runs", "senha-123", roles=["controladoria"])
    svc = PaymentsIngestionService(document_store=store)

    # 2 uploads.
    run_id_a = await svc.queue_upload(
        file_bytes=_fake_xlsx_bytes(), filename="a.xlsx",
        projection_name="supplier_bridge", user_id=user_id,
    )
    run_id_b = await svc.queue_upload(
        file_bytes=_fake_xlsx_bytes(), filename="b.xlsx",
        projection_name="wf_payment", user_id=user_id,
    )

    runs = await svc.list_recent_runs(limit=10)
    # 2 mais recentes (ordem desc), mas TRUNCATE entre tests garante só esses.
    ids = {r["id"] for r in runs}
    assert str(run_id_a) in ids
    assert str(run_id_b) in ids

    # Cada row tem campos pra renderização da tabela.
    expected = {
        "id", "source_filename", "status", "status_label",
        "rows_inserted", "started_at_fmt", "elapsed_fmt",
        "projection_name", "error_message",
    }
    for r in runs:
        assert expected.issubset(r.keys())
        assert r["status_label"] in {"Aguardando", "Em execução", "Concluído", "Falhou"}


@pytest.mark.asyncio
async def test_get_run_returns_none_for_unknown(_payments_schema):
    svc = PaymentsIngestionService()
    out = await svc.get_run(UUID("00000000-0000-0000-0000-000000000000"))
    assert out is None


# ---------------------------------------------------------------------------
# Loader refactor — existing_run_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_source_with_existing_run_id_reuses_run(_payments_schema):
    """Refactor da Fase 3.5: se passar existing_run_id, o loader reusa
    em vez de criar um novo IngestionRun."""
    from app.adapters.sap.projections import (
        ProjectionConfig, load_projection, PROJECTIONS_DIR,
    )
    from app.core.domain.payments import IngestionRun
    from app.core.services.payments.ingestion.loader import load_source
    from app.core.services.payments.ingestion.registry import target_table_for

    repo = PgIngestionRunRepository()
    config = load_projection(PROJECTIONS_DIR / "supplier_bridge.yaml")
    pre = IngestionRun(
        source_type="xlsx",
        source_filename="pre.xlsx",
        source_size_bytes=10,
        target_table=target_table_for(config.target_entity),
        status=IngestionStatus.PENDING,
        metadata={"projection_target": config.target_entity, "via": "ui_test"},
    )
    await repo.create(pre)

    # src_iter vazio → load_source completa sem ler arquivo. Importante:
    # NÃO passa src_iter como gerador mutável; passa lista vazia que é
    # iterable e seguro.
    src_iter = iter([])
    result = await load_source(
        config=config,
        source_path=Path("ignored.xlsx"),
        src_iter=src_iter,
        existing_run_id=pre.id,
    )
    # Mesmo id que foi pré-criado.
    assert result.run.id == pre.id
    # Status virou completed após loader rodar.
    refreshed = await repo.get(pre.id)
    assert refreshed.status == IngestionStatus.COMPLETED
    # E metadata original ('via': 'ui_test') foi preservada — não foi
    # sobrescrita pelo loader.
    assert refreshed.metadata.get("via") == "ui_test"


@pytest.mark.asyncio
async def test_load_source_existing_run_id_unknown_raises(_payments_schema):
    from app.adapters.sap.projections import load_projection, PROJECTIONS_DIR
    from app.core.services.payments.ingestion.loader import load_source

    config = load_projection(PROJECTIONS_DIR / "supplier_bridge.yaml")
    with pytest.raises(ValueError, match="não encontrado"):
        await load_source(
            config=config,
            source_path=Path("ignored.xlsx"),
            src_iter=iter([]),
            existing_run_id=UUID("00000000-0000-0000-0000-000000000000"),
        )


# ---------------------------------------------------------------------------
# Rotas HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingestao_page_renders_with_projection_cards():
    """GET /ingestao: 200, 8 cards de projeção, tabela vazia."""
    from app.main import app

    await _create_user("ctrl_ing", "senha-ing-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_ing", "senha-ing-123")
        resp = await client.get("/payments/empreiteiras-wf/ingestao")
    assert resp.status_code == 200
    body = resp.text
    assert "Ingestão de Arquivos" in body
    # Cards das 8 projeções aparecem (label de cada).
    for p in PROJECTION_CATALOG:
        assert p.label in body, f"label '{p.label}' faltando no template"
    # Tabela vazia.
    assert "nenhuma carga ainda" in body


@pytest.mark.asyncio
async def test_ingestao_page_requires_role():
    from app.main import app

    await _create_user("n3_ing", "senha-ing-123", roles=["analista_n3"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Anônimo → redirect
        resp = await client.get(
            "/payments/empreiteiras-wf/ingestao", follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        # analista_n3 → 403
        await _login_as(client, "n3_ing", "senha-ing-123")
        resp = await client.get("/payments/empreiteiras-wf/ingestao")
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ingestao_upload_route_303_with_run_id(monkeypatch, _payments_schema):
    """POST upload: cria run + despacha actor (stub) + redireciona 303 com
    just_uploaded=<run_id>."""
    from app.main import app

    # Stub do actor: substitui `.send` para não exigir Redis no teste.
    sent: dict[str, dict] = {}
    from app.workers import payments_ingest

    def _fake_send(*args, **kwargs):
        sent["kwargs"] = kwargs
        return type("Msg", (), {"message_id": "fake-msg"})()

    monkeypatch.setattr(payments_ingest.ingest_source, "send", _fake_send)

    await _create_user("ctrl_up", "senha-up-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_up", "senha-up-123")
        resp = await client.post(
            "/payments/empreiteiras-wf/ingestao/upload",
            data={"projection_name": "supplier_bridge"},
            files={"file": ("contratos.xlsx", _fake_xlsx_bytes(),
                            "application/vnd.openxmlformats")},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/payments/empreiteiras-wf/ingestao?just_uploaded=")
    run_id = location.split("=", 1)[1]
    # Actor foi disparado com run_id e projection_name.
    assert sent["kwargs"]["run_id"] == run_id
    assert sent["kwargs"]["projection_name"] == "supplier_bridge"

    # Confere persistência do run.
    repo = PgIngestionRunRepository()
    run = await repo.get(UUID(run_id))
    assert run is not None
    assert run.status == IngestionStatus.PENDING


@pytest.mark.asyncio
async def test_ingestao_upload_rejects_empty_file():
    from app.main import app

    await _create_user("ctrl_empty", "senha-empty-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_empty", "senha-empty-123")
        resp = await client.post(
            "/payments/empreiteiras-wf/ingestao/upload",
            data={"projection_name": "supplier_bridge"},
            files={"file": ("vazio.xlsx", b"", "application/octet-stream")},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_ingestao_upload_rejects_unknown_projection(monkeypatch):
    from app.main import app

    # Mesmo com .send stubado, a validação acontece antes.
    from app.workers import payments_ingest
    monkeypatch.setattr(
        payments_ingest.ingest_source, "send",
        lambda *a, **kw: type("Msg", (), {"message_id": "x"})(),
    )

    await _create_user("ctrl_xx", "senha-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_xx", "senha-123")
        resp = await client.post(
            "/payments/empreiteiras-wf/ingestao/upload",
            data={"projection_name": "xpto_invalida"},
            files={"file": ("a.xlsx", _fake_xlsx_bytes(), "application/octet-stream")},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_ingestao_runs_partial_returns_table_only(_payments_schema, tmp_path):
    """Partial HTMX devolve só o <div id="runs-section"> — sem header,
    sem cards de projeção."""
    from app.main import app
    from app.adapters.storage.filesystem_document_store import (
        FilesystemDocumentStore,
    )

    # Pré-popula 1 run via service (sem actor real).
    store = FilesystemDocumentStore(root=tmp_path)
    user_id = await _create_user("ctrl_partial", "senha-123", roles=["controladoria"])
    svc = PaymentsIngestionService(document_store=store)
    await svc.queue_upload(
        file_bytes=_fake_xlsx_bytes(), filename="seed.xlsx",
        projection_name="supplier_bridge", user_id=user_id,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_partial", "senha-123")
        resp = await client.get("/payments/empreiteiras-wf/ingestao/runs")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="runs-section"' in body
    assert "seed.xlsx" in body
    # Não tem shell completo.
    assert "Ingestão de Arquivos" not in body


@pytest.mark.asyncio
async def test_ingestao_run_status_json(_payments_schema, tmp_path):
    """GET /runs/{run_id} devolve JSON com status atual."""
    from app.main import app
    from app.adapters.storage.filesystem_document_store import (
        FilesystemDocumentStore,
    )

    store = FilesystemDocumentStore(root=tmp_path)
    user_id = await _create_user("ctrl_json", "senha-123", roles=["controladoria"])
    svc = PaymentsIngestionService(document_store=store)
    run_id = await svc.queue_upload(
        file_bytes=_fake_xlsx_bytes(), filename="x.xlsx",
        projection_name="msrv5", user_id=user_id,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_json", "senha-123")
        resp = await client.get(f"/payments/empreiteiras-wf/ingestao/runs/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(run_id)
    assert data["status"] == "pending"
    assert data["status_label"] == "Aguardando"
    assert data["source_filename"] == "x.xlsx"
    # datetimes ISO 8601 (não objeto datetime).
    assert isinstance(data["started_at"], str)


@pytest.mark.asyncio
async def test_ingestao_run_status_404_for_unknown():
    from app.main import app

    await _create_user("ctrl_404", "senha-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_404", "senha-123")
        resp = await client.get(
            "/payments/empreiteiras-wf/ingestao/runs/00000000-0000-0000-0000-000000000000"
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ingestao_run_status_400_for_bad_uuid():
    from app.main import app

    await _create_user("ctrl_bad", "senha-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_bad", "senha-123")
        resp = await client.get("/payments/empreiteiras-wf/ingestao/runs/not-a-uuid")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Bloco B — Polling HTMX + acceptance E2E
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_polls_when_run_is_active(_payments_schema, tmp_path):
    """Quando há run em pending/running, o partial inclui hx-trigger=every 3s.
    Quando todos forem terminais, o trigger some — para o polling sozinho.
    """
    from app.main import app
    from app.adapters.storage.filesystem_document_store import (
        FilesystemDocumentStore,
    )

    store = FilesystemDocumentStore(root=tmp_path)
    user_id = await _create_user("ctrl_poll", "senha-123", roles=["controladoria"])
    svc = PaymentsIngestionService(document_store=store)
    run_id = await svc.queue_upload(
        file_bytes=_fake_xlsx_bytes(), filename="poll.xlsx",
        projection_name="supplier_bridge", user_id=user_id,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_poll", "senha-123")
        resp = await client.get("/payments/empreiteiras-wf/ingestao/runs")
    body = resp.text
    # PENDING → hx-trigger ativo.
    assert 'hx-trigger="every 3s"' in body
    assert 'hx-get="/payments/empreiteiras-wf/ingestao/runs"' in body

    # Marca como completed → polling para.
    repo = PgIngestionRunRepository()
    await repo.mark_completed(
        run_id, rows_read=10, rows_inserted=10, rows_skipped=0, rows_failed=0,
    )
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_poll", "senha-123")
        resp = await client.get("/payments/empreiteiras-wf/ingestao/runs")
    body = resp.text
    assert 'hx-trigger="every 3s"' not in body


@pytest.mark.asyncio
async def test_nav_left_shows_ingestao_entry_under_pagamentos():
    """Sub-entry 'Ingestão' aparece no grupo Pagamentos da nav esquerda
    para usuários com role permitida."""
    from app.main import app

    await _create_user("ctrl_nav_ing", "senha-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_nav_ing", "senha-123")
        resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "Pagamentos" in body
    assert "Empreiteiras WF" in body
    assert "Ingestão" in body
    assert 'href="/payments/empreiteiras-wf/ingestao"' in body


@pytest.mark.asyncio
async def test_acceptance_e2e_ingestao_full_journey(monkeypatch):
    """E2E completo da Fase 3.5:
      1. Login controladoria
      2. GET /ingestao (cards aparecem, tabela vazia)
      3. POST upload com XLSX → 303 com just_uploaded
      4. GET /ingestao?just_uploaded=<id> → banner + linha PENDING na tabela
      5. GET /runs partial → contém o run, com polling ativo (pending)
      6. Simula conclusão (mark_completed direto no repo, como se actor
         tivesse rodado)
      7. GET /runs partial → polling para; status virou Concluído
    """
    from app.main import app
    from app.workers import payments_ingest

    # Stub do actor: aceita .send sem Redis.
    sent: list[dict] = []
    monkeypatch.setattr(
        payments_ingest.ingest_source, "send",
        lambda *a, **kw: sent.append(kw) or type("Msg", (), {"message_id": "x"})(),
    )

    await _create_user("e2e_user", "senha-e2e-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "e2e_user", "senha-e2e-123")

        # 2. GET /ingestao — cards + tabela vazia.
        resp = await client.get("/payments/empreiteiras-wf/ingestao")
        assert resp.status_code == 200
        body = resp.text
        assert "Ingestão de Arquivos" in body
        assert "Contratos-Empreiteiras" in body  # primeiro card
        assert "nenhuma carga ainda" in body

        # 3. POST upload
        resp = await client.post(
            "/payments/empreiteiras-wf/ingestao/upload",
            data={"projection_name": "wf_payment"},
            files={"file": ("analitico_wf.xlsx", _fake_xlsx_bytes(),
                            "application/vnd.openxmlformats")},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        run_id = resp.headers["location"].split("=", 1)[1]
        # Actor disparado uma vez com o run_id.
        assert len(sent) == 1
        assert sent[0]["run_id"] == run_id
        assert sent[0]["projection_name"] == "wf_payment"

        # 4. GET /ingestao com just_uploaded → banner + linha PENDING.
        resp = await client.get(resp.headers["location"])
        assert resp.status_code == 200
        body = resp.text
        assert "Upload enfileirado" in body
        assert "analitico_wf.xlsx" in body
        assert "Aguardando" in body  # status_label do PENDING

        # 5. Partial reflete o pending + polling ativo.
        resp = await client.get("/payments/empreiteiras-wf/ingestao/runs")
        body = resp.text
        assert "analitico_wf.xlsx" in body
        assert 'hx-trigger="every 3s"' in body

        # 6. Simula conclusão (worker rodaria aqui).
        repo = PgIngestionRunRepository()
        await repo.mark_completed(
            UUID(run_id),
            rows_read=869_663, rows_inserted=869_663,
            rows_skipped=0, rows_failed=0,
        )

        # 7. Partial agora sem polling, status Concluído, rows aparecem.
        resp = await client.get("/payments/empreiteiras-wf/ingestao/runs")
        body = resp.text
        assert "Concluído" in body
        assert "869,663" in body  # rows_inserted formatado
        assert 'hx-trigger="every 3s"' not in body

        # Status JSON também reflete.
        resp = await client.get(f"/payments/empreiteiras-wf/ingestao/runs/{run_id}")
        data = resp.json()
        assert data["status"] == "completed"
        assert data["rows_inserted"] == 869_663

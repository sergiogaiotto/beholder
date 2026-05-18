"""Smoke tests da página /payments/empreiteiras-wf (Fase 3 — Bloco A).

Cobre:
  - Service stub: shape do payload do dashboard (header, kpis, charts,
    fornecedores) bate com o esperado pelo template.
  - Constantes: TOTAL_RULES_TARGET = 36 (decisão Fase 3).
  - Rota HTTP via httpx + ASGITransport:
      · não autenticado → 302 para /login
      · autenticado com role 'controladoria' → 200 + título do dashboard
      · autenticado com role 'admin' → 200 (bypass por role permitida)
      · autenticado com role 'analista_n3' → 403 (não está em allowed)

Esses testes ficam estáveis ao longo dos blocos B-F: o shape do payload
não muda, apenas os valores e a origem (mock → query real).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters.db.postgres import init_db
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.services.auth_service import AuthService
from app.core.services.payments.dashboard_service import (
    TOTAL_RULES_TARGET,
    PaymentsDashboardService,
)


# ---------------------------------------------------------------------------
# Service stub — shape do payload (não exige DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_payload_has_expected_top_level_keys():
    svc = PaymentsDashboardService()
    payload = await svc.dashboard_payload()
    assert set(payload.keys()) == {"header", "kpis", "charts", "fornecedores"}


@pytest.mark.asyncio
async def test_header_payload_shape():
    svc = PaymentsDashboardService()
    header = await svc.header_payload()
    assert header["title"].startswith("Monitoramento de Pagamentos")
    assert header["subtitle"] == "Claro S.A."
    re = header["resumo_executivo"]
    # mockup tem 5 fornecedores / 12 contratos / 261 OS.
    assert re == {"fornecedores": 5, "contratos_analisados": 12, "os_analisadas": 261}


@pytest.mark.asyncio
async def test_kpis_returns_exactly_nine_cards():
    svc = PaymentsDashboardService()
    kpis = await svc.kpis()
    assert len(kpis) == 9
    # Toda card precisa dos campos consumidos pelo template.
    expected_fields = {"key", "label", "value", "hint", "icon", "href"}
    for k in kpis:
        assert expected_fields.issubset(k.keys())


@pytest.mark.asyncio
async def test_kpi_acuracidade_uses_total_rules_target():
    svc = PaymentsDashboardService()
    kpis = await svc.kpis()
    acuracidade = next(k for k in kpis if k["key"] == "acuracidade")
    # KPI exibe "Regras: 36/36" — usa a constante do serviço, não literal.
    assert acuracidade["hint"] == f"Regras: {TOTAL_RULES_TARGET}/{TOTAL_RULES_TARGET}"


def test_total_rules_target_is_36():
    # Fase 3 decisão: 20 handlers + 11 R7 + 5 placeholder = 36. Quando R7 entrar,
    # bumpar pra COUNT(*) dinâmico de rule_definition.
    assert TOTAL_RULES_TARGET == 36


@pytest.mark.asyncio
async def test_charts_three_buckets():
    svc = PaymentsDashboardService()
    charts = await svc.charts()
    assert set(charts.keys()) == {
        "alertas_por_tipo", "top_fornecedores", "risco_financeiro",
    }
    # alertas_por_tipo: labels e data alinhados.
    apt = charts["alertas_por_tipo"]
    assert len(apt["labels"]) == len(apt["data"]) == len(apt["colors"])
    # top_fornecedores: cada série tem mesmo length dos labels.
    tf = charts["top_fornecedores"]
    n = len(tf["labels"])
    for serie in tf["series"]:
        assert len(serie["data"]) == n, f"série {serie['name']} dessincronizada"


@pytest.mark.asyncio
async def test_fornecedores_match_mockup():
    svc = PaymentsDashboardService()
    fornecedores = await svc.fornecedores()
    assert len(fornecedores) == 5
    nomes = {f["nome"] for f in fornecedores}
    # Os 5 nomes que aparecem no print 1.
    assert nomes == {
        "ENGEMAN MNT", "EQS ENGENHARIA", "FFA INFRAESTRUTURA",
        "WG PEREIRA", "ABILITY TECNOLOGIA",
    }


# ---------------------------------------------------------------------------
# Rota HTTP — httpx + ASGITransport
# ---------------------------------------------------------------------------


async def _login_as(client: AsyncClient, username: str, password: str) -> None:
    """POST /login para iniciar sessão. O cookie é mantido no client."""
    resp = await client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    # /login retorna 302 em sucesso, 401 em credencial errada.
    assert resp.status_code == 302, f"login falhou: {resp.status_code} / {resp.text[:200]}"


async def _create_user(username: str, password: str, roles: list[str]) -> None:
    await init_db()
    auth = AuthService(PgUserRepository())
    await auth.register(username=username, password=password, roles=roles)


@pytest.mark.asyncio
async def test_anonymous_redirects_to_login():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/payments/empreiteiras-wf", follow_redirects=False)
    # FastAPI RedirectResponse default = 307 (preserva método); 302 também
    # é aceitável quando explicitado. Aceita ambos.
    assert resp.status_code in (302, 307)
    assert "/login" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_controladoria_role_can_access():
    from app.main import app

    await _create_user("ctrl_user", "senha-teste-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_user", "senha-teste-123")
        resp = await client.get("/payments/empreiteiras-wf")
    assert resp.status_code == 200
    body = resp.text
    assert "Monitoramento de Pagamentos para Empreiteiras" in body
    # Verifica que template e service conectaram (KPI renderizado).
    assert "CONTRATOS MONITORADOS" in body
    assert "ACURACIDADE" in body


@pytest.mark.asyncio
async def test_admin_role_can_access():
    from app.main import app

    await _create_user("admin_user", "senha-teste-123", roles=["admin"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "admin_user", "senha-teste-123")
        resp = await client.get("/payments/empreiteiras-wf")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_analista_n3_role_gets_403():
    from app.main import app

    await _create_user("n3_user", "senha-teste-123", roles=["analista_n3"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "n3_user", "senha-teste-123")
        resp = await client.get("/payments/empreiteiras-wf")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_root_bypasses_role_gate():
    from app.main import app

    await _create_user("root_user", "senha-teste-123", roles=["root"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "root_user", "senha-teste-123")
        resp = await client.get("/payments/empreiteiras-wf")
    # Root sempre passa via _require_any_role.
    assert resp.status_code == 200

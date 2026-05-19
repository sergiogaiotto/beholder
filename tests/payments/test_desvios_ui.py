"""Tests da Fase 2.5.1 — UI /desvios (inbox + detalhe analytic_finding).

Cobre:
  - Repo: list_analytic_findings + list_detector_codes_with_findings +
    get_analytic_finding + update_analytic_finding_status
  - Service: desvios_payload + desvio_detail + transition_desvio
  - Rotas HTTP: /desvios, /desvios/{id}, POST /desvios/{id}/decide
  - Matriz de roles consistente com Fase 3
  - Acceptance E2E: inbox → detalhe → decide → status refletido
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters.db.postgres import init_db
from app.adapters.db.postgres_payments import connect_payments, init_payments_schema
from app.adapters.db.repositories.payments.analytics_repos import (
    PgAnalyticFindingRepository,
)
from app.adapters.db.repositories.payments.dashboard_repo import (
    PaymentsDashboardRepository,
)
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.domain.payments import (
    AnalyticFinding,
    FindingStatus,
    Severity,
)
from app.core.services.auth_service import AuthService
from app.core.services.payments.dashboard_service import PaymentsDashboardService


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


async def _get_detector_id(code: str) -> UUID:
    """Seed 007 já popula 11 detectores R7 — busca o id de um."""
    async with connect_payments() as c:
        row = await c.fetchrow(
            "SELECT id FROM payments.analytic_detector WHERE code = $1", code
        )
        assert row is not None, f"detector {code} ausente do seed"
        return row["id"]


async def _seed_analytic_finding(
    *,
    detector_code: str = "R7_LPU_OUTLIER",
    severity: Severity = Severity.MEDIUM,
    score: float = 3.5,
    expected_range: dict | None = None,
    actual_value: dict | None = None,
    supplier_id: UUID | None = None,
) -> str:
    """Cria 1 analytic_finding direto via repo. Devolve o id (str)."""
    detector_id = await _get_detector_id(detector_code)
    finding = AnalyticFinding(
        detector_id=detector_id,
        detector_code=detector_code,
        severity=severity,
        score=score,
        expected_range=expected_range or {"min": 10.0, "max": 100.0, "method": "iqr"},
        actual_value=actual_value or {"valor_unitario": 1000.0},
        supplier_id=supplier_id,
        status=FindingStatus.OPEN,
    )
    await PgAnalyticFindingRepository().create(finding)
    return str(finding.id)


@pytest.fixture
async def _schema():
    await init_db()
    await init_payments_schema()


# ---------------------------------------------------------------------------
# Repo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_analytic_findings_empty_db(_schema):
    repo = PaymentsDashboardRepository()
    page = await repo.list_analytic_findings()
    assert page["total"] == 0
    assert page["rows"] == []
    assert page["page"] == 1
    assert page["pages"] == 1


@pytest.mark.asyncio
async def test_list_analytic_findings_returns_seeded_rows(_schema):
    await _seed_analytic_finding(detector_code="R7_LPU_OUTLIER", severity=Severity.HIGH)
    await _seed_analytic_finding(detector_code="R7_VALIDADE_VENCIDA", severity=Severity.MEDIUM)

    repo = PaymentsDashboardRepository()
    page = await repo.list_analytic_findings()
    assert page["total"] == 2
    codes = {r["detector_code"] for r in page["rows"]}
    assert codes == {"R7_LPU_OUTLIER", "R7_VALIDADE_VENCIDA"}
    # Cada row tem campos consumidos pelo template.
    expected = {
        "id", "detector_code", "severity", "status", "score",
        "expected_range", "actual_value", "detected_at",
        "supplier_nome", "supplier_cnpj",
    }
    for r in page["rows"]:
        assert expected.issubset(r.keys())


@pytest.mark.asyncio
async def test_list_analytic_findings_filters_by_severity_and_detector(_schema):
    await _seed_analytic_finding(detector_code="R7_LPU_OUTLIER", severity=Severity.HIGH)
    await _seed_analytic_finding(detector_code="R7_VALIDADE_VENCIDA", severity=Severity.MEDIUM)

    repo = PaymentsDashboardRepository()
    high = await repo.list_analytic_findings(severity="high")
    assert high["total"] == 1
    assert high["rows"][0]["detector_code"] == "R7_LPU_OUTLIER"

    validade = await repo.list_analytic_findings(detector_code="R7_VALIDADE_VENCIDA")
    assert validade["total"] == 1
    assert validade["rows"][0]["severity"] == "medium"


@pytest.mark.asyncio
async def test_list_detector_codes_with_findings(_schema):
    await _seed_analytic_finding(detector_code="R7_LPU_OUTLIER")
    await _seed_analytic_finding(detector_code="R7_VALIDADE_VENCIDA")
    repo = PaymentsDashboardRepository()
    codes = await repo.list_detector_codes_with_findings()
    assert codes == ["R7_LPU_OUTLIER", "R7_VALIDADE_VENCIDA"]  # ordenado alfa


@pytest.mark.asyncio
async def test_get_analytic_finding_returns_full_detail(_schema):
    fid = await _seed_analytic_finding(
        detector_code="R7_LPU_OUTLIER",
        severity=Severity.HIGH,
        score=4.2,
        expected_range={"min": 10.0, "max": 50.0, "method": "iqr"},
        actual_value={"valor_unitario": 200.0, "material": "SRV001"},
    )

    repo = PaymentsDashboardRepository()
    detail = await repo.get_analytic_finding(fid)
    assert detail is not None
    assert detail["id"] == fid
    assert detail["detector_code"] == "R7_LPU_OUTLIER"
    assert detail["score"] == 4.2
    # JOIN detector trouxe name + description + technique.
    assert detail["detector_name"]  # seed populou
    assert detail["detector_technique"]
    # JSONB unwrapped pra dict.
    assert detail["expected_range"]["method"] == "iqr"
    assert detail["actual_value"]["valor_unitario"] == 200.0


@pytest.mark.asyncio
async def test_get_analytic_finding_returns_none_for_unknown(_schema):
    repo = PaymentsDashboardRepository()
    detail = await repo.get_analytic_finding("00000000-0000-0000-0000-000000000000")
    assert detail is None


@pytest.mark.asyncio
async def test_update_analytic_finding_status_persists(_schema):
    fid = await _seed_analytic_finding()
    user_id = str(await _create_user("ctrl_upd", "senha-123", roles=["controladoria"]))

    repo = PaymentsDashboardRepository()
    ok = await repo.update_analytic_finding_status(
        fid, new_status="in_analysis",
        decision_reason="investigando", decided_by_user_id=user_id,
    )
    assert ok is True

    refreshed = await repo.get_analytic_finding(fid)
    assert refreshed["status"] == "in_analysis"
    assert refreshed["decision_reason"] == "investigando"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_desvios_payload_empty_db(_schema):
    svc = PaymentsDashboardService()
    payload = await svc.desvios_payload()
    assert payload["findings"]["total"] == 0
    assert payload["filtros"]["detector_codes"] == []
    # severity_options sempre fixos (high/medium/low).
    assert len(payload["filtros"]["severity_options"]) == 3
    assert len(payload["filtros"]["statuses"]) == 5


@pytest.mark.asyncio
async def test_desvios_payload_enriches_labels(_schema):
    await _seed_analytic_finding(detector_code="R7_LPU_OUTLIER", severity=Severity.HIGH)
    svc = PaymentsDashboardService()
    payload = await svc.desvios_payload()
    row = payload["findings"]["rows"][0]
    assert row["severity_label"] == "Crítico"
    assert row["status_label"] == "Aberto"
    assert row["score_fmt"].startswith(("+", "-"))


@pytest.mark.asyncio
async def test_desvio_detail_returns_none_for_unknown(_schema):
    svc = PaymentsDashboardService()
    detail = await svc.desvio_detail("00000000-0000-0000-0000-000000000000")
    assert detail is None


@pytest.mark.asyncio
async def test_desvio_detail_includes_transitions(_schema):
    fid = await _seed_analytic_finding()
    svc = PaymentsDashboardService()
    detail = await svc.desvio_detail(fid)
    assert detail["status"] == "open"
    transitions = {t["key"] for t in detail["available_transitions"]}
    assert transitions == {"in_analysis", "escalated", "accepted_fp", "blocked"}
    assert detail["is_terminal"] is False


@pytest.mark.asyncio
async def test_transition_desvio_valid_flow(_schema):
    fid = await _seed_analytic_finding()
    user_id = str(await _create_user("ctrl_t", "senha-123", roles=["controladoria"]))
    svc = PaymentsDashboardService()
    ok, err = await svc.transition_desvio(
        fid, new_status="accepted_fp",
        decision_reason="falso positivo", decided_by_user_id=user_id,
    )
    assert ok and err is None
    detail = await svc.desvio_detail(fid)
    assert detail["status"] == "accepted_fp"
    assert detail["is_terminal"] is True


@pytest.mark.asyncio
async def test_transition_desvio_rejects_invalid(_schema):
    fid = await _seed_analytic_finding()
    user_id = str(await _create_user("ctrl_inv", "senha-123", roles=["controladoria"]))
    svc = PaymentsDashboardService()
    # Move pra terminal primeiro.
    await svc.transition_desvio(
        fid, new_status="accepted_fp",
        decision_reason=None, decided_by_user_id=user_id,
    )
    # Tentar sair do terminal — rejeitado.
    ok, err = await svc.transition_desvio(
        fid, new_status="open",
        decision_reason=None, decided_by_user_id=user_id,
    )
    assert not ok
    assert err and "transição inválida" in err


# ---------------------------------------------------------------------------
# Rotas HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_desvios_page_renders(_schema):
    from app.main import app

    await _seed_analytic_finding(detector_code="R7_LPU_OUTLIER", severity=Severity.HIGH)
    await _create_user("ctrl_pg", "senha-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_pg", "senha-123")
        resp = await client.get("/payments/empreiteiras-wf/desvios")
    assert resp.status_code == 200
    body = resp.text
    assert "Inbox de Desvios (R7)" in body
    assert "R7_LPU_OUTLIER" in body
    assert "Crítico" in body


@pytest.mark.asyncio
async def test_desvios_route_requires_role():
    from app.main import app

    await _create_user("n3_desv", "senha-123", roles=["analista_n3"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Anônimo → redirect.
        resp = await client.get(
            "/payments/empreiteiras-wf/desvios", follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        # analista_n3 → 403.
        await _login_as(client, "n3_desv", "senha-123")
        resp = await client.get("/payments/empreiteiras-wf/desvios")
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_desvio_detalhe_route_404_for_unknown(_schema):
    from app.main import app

    await _create_user("ctrl_404", "senha-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_404", "senha-123")
        resp = await client.get(
            "/payments/empreiteiras-wf/desvios/00000000-0000-0000-0000-000000000000"
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_acceptance_e2e_desvios_full_journey(_schema):
    """E2E: inbox → filtra por severity → detalhe → decide → status."""
    from app.main import app

    fid = await _seed_analytic_finding(
        detector_code="R7_LPU_OUTLIER", severity=Severity.HIGH, score=5.2,
    )
    await _create_user("e2e_desv", "senha-e2e-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "e2e_desv", "senha-e2e-123")

        # 1. Inbox renderiza com o desvio seedado.
        resp = await client.get("/payments/empreiteiras-wf/desvios")
        assert resp.status_code == 200
        body = resp.text
        assert "R7_LPU_OUTLIER" in body
        assert "Crítico" in body
        assert ">Aberto<" in body

        # 2. Filtra por severity=high — só o mesmo desvio.
        resp = await client.get(
            "/payments/empreiteiras-wf/desvios",
            params={"severity": "high"},
        )
        assert "R7_LPU_OUTLIER" in resp.text

        # 3. Detalhe.
        resp = await client.get(f"/payments/empreiteiras-wf/desvios/{fid}")
        assert resp.status_code == 200
        body = resp.text
        assert "R7_LPU_OUTLIER" in body
        assert ">Aberto<" in body
        assert 'name="new_status"' in body  # form decisão visível

        # 4. POST decide → in_analysis (303 PRG).
        resp = await client.post(
            f"/payments/empreiteiras-wf/desvios/{fid}/decide",
            data={"new_status": "in_analysis", "decision_reason": "investigando E2E"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert fid in resp.headers["location"]

        # 5. Segue o redirect → status atualizado + motivo persistido.
        resp = await client.get(resp.headers["location"])
        assert resp.status_code == 200
        body = resp.text
        assert ">Em Análise<" in body
        assert "investigando E2E" in body
        assert "e2e_desv" in body  # decided_by_username


@pytest.mark.asyncio
async def test_acceptance_nav_left_shows_desvios_entry():
    """Cockpit renderiza nav com entry 'Desvios (R7)' apontando pra /desvios."""
    from app.main import app

    await _create_user("ctrl_nav_desv", "senha-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_nav_desv", "senha-123")
        resp = await client.get("/")
    body = resp.text
    assert "Desvios (R7)" in body
    assert 'href="/payments/empreiteiras-wf/desvios"' in body

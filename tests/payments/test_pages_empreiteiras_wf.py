"""Tests da página /payments/empreiteiras-wf (Fase 3 — Blocos A + B).

Cobre:
  - Helpers do service: `_fmt_brl`, `_fmt_pct`, `_safe_pct`.
  - Service com DB vazio: shape do payload + zeros + "—" para médias sem
    amostra (tempo_medio e delta_pct_avg).
  - Service com seed mínimo (contratos + wf_payments + findings + run):
    verifica que cada KPI computa corretamente.
  - Rota HTTP via httpx + ASGITransport:
      · não autenticado → 307 para /login
      · autenticado com role 'controladoria' → 200 + título do dashboard
      · autenticado com role 'admin' → 200 (bypass por role permitida)
      · autenticado com role 'analista_n3' → 403
      · autenticado com role 'root' → 200 (bypass total)

Cada teste com DB usa o pool dedicado `payments` (vide
memory/payments_pool_quirks.md) e relé no autouse `_reset_payments_schema`
do conftest local + TRUNCATE do conftest pai.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters.db.postgres import init_db
from app.adapters.db.postgres_payments import connect_payments, init_payments_schema
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.services.auth_service import AuthService
from app.core.services.payments.dashboard_service import (
    TOTAL_RULES_TARGET,
    PaymentsDashboardService,
    _fmt_brl,
    _fmt_pct,
    _safe_pct,
)


# ---------------------------------------------------------------------------
# Pure helpers — sem DB
# ---------------------------------------------------------------------------


def test_fmt_brl_basic():
    assert _fmt_brl(Decimal("447010.71")) == "R$ 447.010,71"
    assert _fmt_brl(0) == "R$ 0,00"
    assert _fmt_brl(Decimal("1234567.89")) == "R$ 1.234.567,89"


def test_fmt_pct_signed_and_unsigned():
    assert _fmt_pct(0.011) == "1.1%"
    assert _fmt_pct(1.053, signed=True) == "+105.3%"
    assert _fmt_pct(-0.05, signed=True) == "-5.0%"


def test_safe_pct_zero_denominator_returns_none():
    assert _safe_pct(10, 0) is None
    assert _safe_pct(0, 100) == 0.0
    assert _safe_pct(50, 200) == 0.25


def test_total_rules_target_is_36():
    # Fase 3 decisão: 20 handlers + 11 R7 + 5 placeholder = 36. Quando R7 entrar,
    # bumpar pra COUNT(*) dinâmico de rule_definition.
    assert TOTAL_RULES_TARGET == 36


# ---------------------------------------------------------------------------
# Service com DB vazio — shape garantido, valores zerados, '—' para médias
# ---------------------------------------------------------------------------


@pytest.fixture
async def _ensure_payments_schema():
    """Garante que o schema `payments` está criado para queries de leitura
    funcionarem em DB vazio (sem o init_db do app)."""
    await init_db()
    await init_payments_schema()


@pytest.mark.asyncio
async def test_dashboard_payload_has_expected_top_level_keys(_ensure_payments_schema):
    svc = PaymentsDashboardService()
    payload = await svc.dashboard_payload()
    assert set(payload.keys()) == {"header", "kpis", "charts", "fornecedores"}


@pytest.mark.asyncio
async def test_header_payload_shape(_ensure_payments_schema):
    svc = PaymentsDashboardService()
    header = await svc.header_payload()
    assert header["title"].startswith("Monitoramento de Pagamentos")
    assert header["subtitle"] == "Claro S.A."
    re = header["resumo_executivo"]
    # DB vazio: todos zerados.
    assert re == {"fornecedores": 0, "contratos_analisados": 0, "os_analisadas": 0}


@pytest.mark.asyncio
async def test_kpis_with_empty_db_returns_nine_zeroed_cards(_ensure_payments_schema):
    svc = PaymentsDashboardService()
    kpis = await svc.kpis()
    assert len(kpis) == 9
    # Toda card precisa dos campos consumidos pelo template.
    expected_fields = {"key", "label", "value", "hint", "icon", "href"}
    for k in kpis:
        assert expected_fields.issubset(k.keys())

    by_key = {k["key"]: k for k in kpis}
    # Numéricos zerados.
    assert by_key["contratos_monitorados"]["value"] == "0"
    assert by_key["os_analisadas"]["value"] == "0"
    assert by_key["total_alertas"]["value"] == "0"
    assert by_key["risco_financeiro"]["value"] == "R$ 0,00"
    # Percentuais e médias sem amostra → '—'.
    assert by_key["pct_risco"]["value"] == "—"
    assert by_key["taxa_recorrencia"]["value"] == "—"
    assert by_key["tempo_medio"]["value"] == "—"
    # Δ médio LPU sem amostra também '—'.
    assert "—" in by_key["comparativo_lpu"]["hint"]
    # Acuracidade: 0/36 = 0.0%.
    assert by_key["acuracidade"]["value"] == "0.0%"
    assert by_key["acuracidade"]["hint"] == f"Regras: 0/{TOTAL_RULES_TARGET}"


@pytest.mark.asyncio
async def test_charts_three_buckets(_ensure_payments_schema):
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
async def test_fornecedores_match_mockup_still_mock(_ensure_payments_schema):
    svc = PaymentsDashboardService()
    fornecedores = await svc.fornecedores()
    assert len(fornecedores) == 5
    # Tabela ainda é mock no Bloco B (Bloco D conecta DB).
    nomes = {f["nome"] for f in fornecedores}
    assert nomes == {
        "ENGEMAN MNT", "EQS ENGENHARIA", "FFA INFRAESTRUTURA",
        "WG PEREIRA", "ABILITY TECNOLOGIA",
    }


# ---------------------------------------------------------------------------
# Service com seed — KPIs computados de verdade
# ---------------------------------------------------------------------------


async def _create_test_user_id() -> str:
    """Cria um user e devolve o UUID; necessário para satisfazer FKs em
    contract_master.created_by_id."""
    await init_db()
    auth = AuthService(PgUserRepository())
    user = await auth.register(
        username=f"seed_{uuid4().hex[:6]}",
        password="seed-pass-123",
        roles=["admin"],
    )
    return str(user.id)


async def _seed_minimal_dataset() -> None:
    """Insere 2 suppliers + 3 contratos (2 monitorados, 1 não) + 2 wf_payments
    (1 in-universe, 1 out) + 1 reconciliation_run + 2 findings (1 LPU, 1 R5).

    Suficiente para validar todos os 9 KPIs simultaneamente.
    """
    await init_payments_schema()
    user_id = await _create_test_user_id()

    # IDs estáveis para asserts cruzadas.
    sup_a = str(uuid4())
    sup_b = str(uuid4())
    cm_a1 = str(uuid4())
    cm_a2 = str(uuid4())
    cm_b1 = str(uuid4())
    run_id = str(uuid4())

    async with connect_payments() as c:
        # Reusa REGRA_LPU do seed (migration 007); não cria duplicata.
        rule_id = await c.fetchval(
            "SELECT id FROM payments.rule_definition WHERE code = 'REGRA_LPU'"
        )
        assert rule_id is not None, "seed 007 não populou REGRA_LPU"
        # Suppliers
        await c.execute(
            """
            INSERT INTO payments.supplier_bridge (id, categoria, empreiteira, contrato_num_sap, ref_ws, numero_fornecedor_sap, cnpj)
            VALUES ($1, 'INSTALACAO', 'ENGEMAN MNT', '4600000001', 'WS001', '100001', '01731483000167'),
                   ($2, 'INSTALACAO', 'EQS ENGENHARIA', '4600000002', 'WS002', '100002', '80464753000197')
            """,
            sup_a, sup_b,
        )
        # Contracts: 2 monitorados, 1 não monitorado (alimenta kpi_contratos)
        await c.execute(
            """
            INSERT INTO payments.contract_master (id, supplier_bridge_id, contrato_num_sap, ref_ws, cnpj, is_monitored, created_by_id)
            VALUES ($1, $4, '4600000001', 'WS001', '01731483000167', TRUE,  $7::uuid),
                   ($2, $5, '4600000002', 'WS002', '80464753000197', TRUE,  $7::uuid),
                   ($3, $6, '4600000003', 'WS003', '11111111000111', FALSE, $7::uuid)
            """,
            cm_a1, cm_a2, cm_b1, sup_a, sup_b, sup_a, user_id,
        )
        # WF payments: 2 dentro do universo, 1 fora (status_os bloqueia)
        await c.execute(
            """
            INSERT INTO payments.wf_payment
                (os_num, sistema, empreiteira, data_pedido, valor_total_final,
                 status_os, nivel_gerencial, malogro)
            VALUES
                ('OS-001', 'WF1', 'ENGEMAN MNT', '2025-06-01', 100000.00,
                 'EXECUTADO', 'Em Pagamento', 'OK'),
                ('OS-002', 'WF1', 'EQS ENGENHARIA', '2025-06-15',  50000.00,
                 'EXECUTADO', 'Em Pagamento', 'OK'),
                ('OS-003', 'WF1', 'FFA INFRAESTRUTURA', '2025-06-20', 999999.00,
                 'CANCELADO', 'Em Pagamento', 'OK')
            """,
        )
        # Run + findings
        await c.execute(
            """
            INSERT INTO payments.reconciliation_run
                (id, triggered_by, rules_executed, status, started_at, finished_at)
            VALUES ($1, 'manual', ARRAY['REGRA_1','REGRA_LPU','REGRA_3'],
                    'completed', NOW() - INTERVAL '2 days', NOW() - INTERVAL '2 days' + INTERVAL '1 hour')
            """,
            run_id,
        )
        # 2 findings: ambos abertos, 1 da REGRA_LPU (alimenta lpu_resumo).
        await c.execute(
            """
            INSERT INTO payments.reconciliation_finding
                (run_id, rule_id, rule_code, severity, status,
                 purchase_order_documento, wf_payment_data_pedido,
                 supplier_id, expected_value, actual_value,
                 delta_pct, value_at_risk_brl, detected_at)
            VALUES
                ($1, $2, 'REGRA_LPU', 'high', 'open',
                 '4500000001', '2025-06-01', $3,
                 '{"preco_lpu": 10.0}'::jsonb, '{"preco_pago": 21.0}'::jsonb,
                 1.10, 1000.00, NOW() - INTERVAL '2 days'),
                ($1, $2, 'REGRA_5_UF', 'medium', 'open',
                 '4500000002', '2025-06-15', $4,
                 '{"uf_lpu": "RJ"}'::jsonb, '{"uf_wf": "SP"}'::jsonb,
                 NULL, 500.00, NOW() - INTERVAL '1 day')
            """,
            run_id, rule_id, sup_a, sup_b,
        )


@pytest.mark.asyncio
async def test_kpi_contratos_after_seed():
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    buckets = await svc._fetch_kpi_buckets()
    contratos = buckets["contratos"]
    assert contratos["monitorados"] == 2
    assert contratos["nao_monitorados"] == 1
    assert contratos["fornecedores"] == 2  # supplier_a + supplier_b


@pytest.mark.asyncio
async def test_kpi_os_filters_universe():
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    buckets = await svc._fetch_kpi_buckets()
    os_ = buckets["os"]
    # OS-003 (CANCELADO) é excluída pelo filtro universal.
    assert os_["os_count"] == 2
    assert os_["fornecedores"] == 2


@pytest.mark.asyncio
async def test_kpi_alertas_and_risco():
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    buckets = await svc._fetch_kpi_buckets()
    alertas = buckets["alertas"]
    assert alertas["total"] == 2  # 2 findings 'open'
    assert alertas["risco_brl"] == Decimal("1500.00")  # 1000 + 500
    # Total analisado = soma dos 2 wf_payments in-universe: 100k + 50k.
    assert alertas["total_analisado_brl"] == Decimal("150000.00")


@pytest.mark.asyncio
async def test_kpi_lpu_finds_regra_lpu_only():
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    buckets = await svc._fetch_kpi_buckets()
    lpu = buckets["lpu"]
    # Só o finding REGRA_LPU conta: value=1000, delta=1.10.
    assert lpu["total_brl"] == Decimal("1000.00")
    assert lpu["delta_pct_avg"] == pytest.approx(1.10)


@pytest.mark.asyncio
async def test_kpi_acuracidade_counts_distinct_rules_executed():
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    buckets = await svc._fetch_kpi_buckets()
    # O seed cria run com rules_executed = [REGRA_1, REGRA_LPU, REGRA_3].
    assert buckets["acuracidade"]["executed_ok"] == 3


@pytest.mark.asyncio
async def test_full_payload_after_seed_smoke():
    """Smoke: dashboard_payload() roda sem erro e devolve KPIs formatados
    com os números do seed."""
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    payload = await svc.dashboard_payload()
    by_key = {k["key"]: k for k in payload["kpis"]}

    assert by_key["contratos_monitorados"]["value"] == "2"
    assert by_key["os_analisadas"]["value"] == "2"
    assert by_key["total_alertas"]["value"] == "2"
    assert by_key["risco_financeiro"]["value"] == "R$ 1.500,00"
    # % risco = 1500/150000 = 1.0%.
    assert by_key["pct_risco"]["value"] == "1.0%"
    # Δ médio LPU = +110.0%.
    assert "+110.0%" in by_key["comparativo_lpu"]["hint"]
    # Acuracidade = 3/36 = 8.3%.
    assert by_key["acuracidade"]["value"] == "8.3%"
    assert by_key["acuracidade"]["hint"] == f"Regras: 3/{TOTAL_RULES_TARGET}"

    # Header também reflete o seed.
    re = payload["header"]["resumo_executivo"]
    assert re["fornecedores"] == 2
    assert re["contratos_analisados"] == 2
    assert re["os_analisadas"] == 2


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
    # FastAPI RedirectResponse default = 307; 302 também aceito.
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
    assert resp.status_code == 200

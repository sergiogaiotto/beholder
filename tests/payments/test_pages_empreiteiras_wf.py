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
    assert set(payload.keys()) == {
        "header", "kpis", "charts", "fornecedores", "filtros", "active_filters",
    }


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
async def test_charts_three_buckets_with_empty_db(_ensure_payments_schema):
    """DB vazio: 3 buckets presentes, alertas_por_tipo sempre tem 3
    categorias (zeradas), top_fornecedores e risco_financeiro vazios."""
    svc = PaymentsDashboardService()
    charts = await svc.charts()
    assert set(charts.keys()) == {
        "alertas_por_tipo", "top_fornecedores", "risco_financeiro",
    }
    # alertas_por_tipo: 3 labels fixos (Alerta Op./Proc./St. Atípica) mesmo vazio.
    apt = charts["alertas_por_tipo"]
    assert apt["labels"] == ["Alerta Op.", "Alerta Proc.", "St. Atípica"]
    assert apt["data"] == [0, 0, 0]
    assert len(apt["colors"]) == 3
    # top_fornecedores: labels vazios, mas as 3 séries existem.
    tf = charts["top_fornecedores"]
    assert tf["labels"] == []
    assert [s["name"] for s in tf["series"]] == ["Alerta Op.", "Alerta Proc.", "St. Atípica"]
    for serie in tf["series"]:
        assert serie["data"] == []
    # risco_financeiro vazio.
    rf = charts["risco_financeiro"]
    assert rf["labels"] == []
    assert rf["data"] == []


@pytest.mark.asyncio
async def test_charts_with_seed_data():
    """Com seed: charts refletem os 2 findings (1 high REGRA_LPU, 1 medium R5_UF)."""
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    charts = await svc.charts()

    # Donut: 1 high → Alerta Op.=1, 1 medium → Alerta Proc.=1, low=0.
    apt = charts["alertas_por_tipo"]
    assert apt["data"] == [1, 1, 0]

    # Top fornecedores: ENGEMAN MNT (1 high) e EQS ENGENHARIA (1 medium).
    tf = charts["top_fornecedores"]
    assert set(tf["labels"]) == {"ENGEMAN MNT", "EQS ENGENHARIA"}
    # Stack do high
    by_label = dict(zip(tf["labels"], tf["series"][0]["data"]))
    assert by_label["ENGEMAN MNT"] == 1  # 1 finding high
    assert by_label["EQS ENGENHARIA"] == 0
    # Stack do medium
    by_label_med = dict(zip(tf["labels"], tf["series"][1]["data"]))
    assert by_label_med["ENGEMAN MNT"] == 0
    assert by_label_med["EQS ENGENHARIA"] == 1

    # Risco financeiro: ENGEMAN MNT (R$ 1000), EQS ENGENHARIA (R$ 500).
    rf = charts["risco_financeiro"]
    assert rf["labels"] == ["ENGEMAN MNT", "EQS ENGENHARIA"]
    assert rf["data"] == [1000.0, 500.0]


@pytest.mark.asyncio
async def test_fornecedores_table_after_seed():
    """Sem filtros: tabela mostra os 2 fornecedores monitorados (3o não
    monitorado é excluído)."""
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    fornecedores = await svc.fornecedores()
    nomes = {f["nome"] for f in fornecedores}
    assert nomes == {"ENGEMAN MNT", "EQS ENGENHARIA"}
    # CNPJs do seed.
    by_nome = {f["nome"]: f for f in fornecedores}
    assert by_nome["ENGEMAN MNT"]["cnpj"] == "01731483000167"
    assert by_nome["EQS ENGENHARIA"]["cnpj"] == "80464753000197"


@pytest.mark.asyncio
async def test_fornecedores_filter_by_search():
    """`search` aplica ILIKE em empreiteira ou CNPJ."""
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    # ENGEMAN MNT só
    rows = await svc.fornecedores(search="ENGEMAN")
    assert [r["nome"] for r in rows] == ["ENGEMAN MNT"]
    # Por CNPJ parcial
    rows = await svc.fornecedores(search="80464")
    assert [r["nome"] for r in rows] == ["EQS ENGENHARIA"]
    # Vazio
    rows = await svc.fornecedores(search="XYZ_INEXISTENTE")
    assert rows == []


@pytest.mark.asyncio
async def test_fornecedores_filter_by_tipo_maps_to_severity():
    """Filtro tipo='Alerta Op.' (label) → severity='high' no repo.

    Seed cria 1 finding high (ENGEMAN) e 1 medium (EQS).
    """
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    op = await svc.fornecedores(tipo="Alerta Op.")
    assert [r["nome"] for r in op] == ["ENGEMAN MNT"]
    proc = await svc.fornecedores(tipo="Alerta Proc.")
    assert [r["nome"] for r in proc] == ["EQS ENGENHARIA"]
    # Label não mapeado vira no-op (devolve todos).
    invalido = await svc.fornecedores(tipo="Inexistente")
    assert len(invalido) == 2


@pytest.mark.asyncio
async def test_chart_top_fornecedores_respects_limit():
    """`chart_top_fornecedores(limit=N)` corta no N. Seed cria só 2 → limite=5
    devolve 2; limite=1 devolve só o mais arriscado."""
    await _seed_minimal_dataset()
    from app.adapters.db.repositories.payments.dashboard_repo import (
        PaymentsDashboardRepository,
    )
    repo = PaymentsDashboardRepository()
    rows_5 = await repo.chart_top_fornecedores(limit=5)
    rows_1 = await repo.chart_top_fornecedores(limit=1)
    assert len(rows_5) == 2
    assert len(rows_1) == 1
    # O #1 é ENGEMAN MNT (high vale 3 pontos vs medium 2 da EQS).
    assert rows_1[0]["empreiteira"] == "ENGEMAN MNT"


@pytest.mark.asyncio
async def test_fornecedores_empty_db_returns_empty_list(_ensure_payments_schema):
    """DB vazio: tabela sem linhas (não mais mock após Bloco D)."""
    svc = PaymentsDashboardService()
    fornecedores = await svc.fornecedores()
    assert fornecedores == []


@pytest.mark.asyncio
async def test_filtros_disponiveis_with_empty_db(_ensure_payments_schema):
    svc = PaymentsDashboardService()
    filtros = await svc.filtros_disponiveis()
    assert filtros["tipos_alerta"] == ["Alerta Op.", "Alerta Proc.", "St. Atípica"]
    assert filtros["ufs"] == []


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


@pytest.mark.asyncio
async def test_fornecedores_partial_route_renders_only_tbody():
    """A rota HTMX /fornecedores devolve só o partial — sem header, sem
    KPIs, sem charts. Inicia com tbody#fornecedores-tbody."""
    from app.main import app

    await _seed_minimal_dataset()
    await _create_user("ctrl2", "senha-teste-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl2", "senha-teste-123")
        resp = await client.get("/payments/empreiteiras-wf/fornecedores")
    assert resp.status_code == 200
    body = resp.text
    # Partial só renderiza o tbody — nada do shell completo.
    assert "<tbody id=\"fornecedores-tbody\">" in body
    assert "ENGEMAN MNT" in body
    assert "EQS ENGENHARIA" in body
    # Não tem header vermelho nem KPIs.
    assert "CONTRATOS MONITORADOS" not in body
    assert "Monitoramento de Pagamentos" not in body


@pytest.mark.asyncio
async def test_fornecedores_partial_route_applies_filters():
    """Filtros query-string passam pro service via partial."""
    from app.main import app

    await _seed_minimal_dataset()
    await _create_user("ctrl3", "senha-teste-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl3", "senha-teste-123")
        resp = await client.get(
            "/payments/empreiteiras-wf/fornecedores",
            params={"search": "ENGEMAN"},
        )
    assert resp.status_code == 200
    body = resp.text
    assert "ENGEMAN MNT" in body
    assert "EQS ENGENHARIA" not in body


@pytest.mark.asyncio
async def test_inbox_payload_empty_db(_ensure_payments_schema):
    """Inbox em DB vazio: 0 findings, paginação coerente, catálogos vazios."""
    svc = PaymentsDashboardService()
    inbox = await svc.inbox_payload()
    assert inbox["findings"]["total"] == 0
    assert inbox["findings"]["rows"] == []
    assert inbox["findings"]["page"] == 1
    assert inbox["findings"]["pages"] == 1  # min 1 mesmo com 0
    assert inbox["filtros"]["rule_codes"] == []
    # Statuses sempre fixos (5 estados do workflow).
    assert len(inbox["filtros"]["statuses"]) == 5


@pytest.mark.asyncio
async def test_inbox_payload_after_seed_shows_both_findings():
    """Seed cria 2 findings ambos open — inbox os retorna por padrão."""
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    inbox = await svc.inbox_payload()
    assert inbox["findings"]["total"] == 2
    items = inbox["findings"]["rows"]
    assert {it["rule_code"] for it in items} == {"REGRA_LPU", "REGRA_5_UF"}
    # Cada item tem campos formatados.
    for it in items:
        assert it["value_at_risk_brl_fmt"].startswith("R$")
        assert it["severity_label"] in ("Alerta Op.", "Alerta Proc.", "St. Atípica")
        assert it["status_label"] == "Aberto"
        assert "/" in it["detected_at_fmt"]  # data dd/mm/YYYY


@pytest.mark.asyncio
async def test_inbox_filters_by_severity():
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    inbox = await svc.inbox_payload(severity="high")
    assert inbox["findings"]["total"] == 1
    assert inbox["findings"]["rows"][0]["rule_code"] == "REGRA_LPU"


@pytest.mark.asyncio
async def test_inbox_filters_by_rule_code_and_search():
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    # Filtro por rule_code
    inbox = await svc.inbox_payload(rule_code="REGRA_5_UF")
    assert inbox["findings"]["total"] == 1
    # Filtro por search no rule_code
    inbox = await svc.inbox_payload(search="LPU")
    assert inbox["findings"]["total"] == 1
    # Filtro por search no purchase_order
    inbox = await svc.inbox_payload(search="4500000002")
    assert inbox["findings"]["total"] == 1
    assert inbox["findings"]["rows"][0]["rule_code"] == "REGRA_5_UF"


@pytest.mark.asyncio
async def test_inbox_pagination_basic():
    """per_page=1 dividindo 2 findings → 2 páginas."""
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    p1 = await svc.inbox_payload(per_page=1, page=1)
    p2 = await svc.inbox_payload(per_page=1, page=2)
    assert p1["findings"]["total"] == 2
    assert p1["findings"]["pages"] == 2
    assert len(p1["findings"]["rows"]) == 1
    assert len(p2["findings"]["rows"]) == 1
    # IDs diferentes nas duas páginas (sem overlap).
    assert p1["findings"]["rows"][0]["id"] != p2["findings"]["rows"][0]["id"]


@pytest.mark.asyncio
async def test_alertas_route_renders_with_seed():
    """Rota /alertas: 200, tabela com 2 findings, breadcrumb pra dashboard."""
    from app.main import app

    await _seed_minimal_dataset()
    await _create_user("ctrl_alertas", "senha-teste-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_alertas", "senha-teste-123")
        resp = await client.get("/payments/empreiteiras-wf/alertas")
    assert resp.status_code == 200
    body = resp.text
    assert "Inbox de Alertas" in body
    assert "REGRA_LPU" in body
    assert "REGRA_5_UF" in body
    # Breadcrumb pro dashboard.
    assert 'href="/payments/empreiteiras-wf"' in body


@pytest.mark.asyncio
async def test_alertas_route_filter_via_querystring():
    """Filtro severity passa pelo querystring e converte label→severity."""
    from app.main import app

    await _seed_minimal_dataset()
    await _create_user("ctrl_alertas2", "senha-teste-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_alertas2", "senha-teste-123")
        resp = await client.get(
            "/payments/empreiteiras-wf/alertas",
            params={"severity": "Alerta Op."},
        )
    assert resp.status_code == 200
    body = resp.text
    # Só o REGRA_LPU (severity=high) aparece na tabela. REGRA_5_UF (medium)
    # ainda aparece no <option> do dropdown 'Regra' (catálogo completo),
    # então comparo a célula da tabela (font-mono text-xs no <td>).
    assert "REGRA_LPU" in body
    assert '<td class="py-2 px-3 font-mono text-xs">REGRA_5_UF</td>' not in body


@pytest.mark.asyncio
async def test_finding_detail_returns_none_for_unknown_id(_ensure_payments_schema):
    svc = PaymentsDashboardService()
    detail = await svc.finding_detail("00000000-0000-0000-0000-000000000000")
    assert detail is None


@pytest.mark.asyncio
async def test_finding_detail_after_seed():
    """Detalhe traz JOINs (rule, supplier, run) e campos formatados."""
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    inbox = await svc.inbox_payload(rule_code="REGRA_LPU")
    finding_id = inbox["findings"]["rows"][0]["id"]
    detail = await svc.finding_detail(finding_id)
    assert detail is not None
    assert detail["rule_code"] == "REGRA_LPU"
    assert detail["rule_name"] == "LPU compare" or detail["rule_name"]  # seed real
    assert detail["supplier_nome"] == "ENGEMAN MNT"
    assert detail["supplier_cnpj"] == "01731483000167"
    assert detail["value_at_risk_brl_fmt"] == "R$ 1.000,00"
    assert detail["delta_pct_fmt"] == "+110.0%"
    assert detail["severity_label"] == "Alerta Op."
    assert detail["status_label"] == "Aberto"
    # expected_value / actual_value vêm como dict (JSONB unwrapped).
    assert detail["expected_value"]["preco_lpu"] == 10.0
    assert detail["actual_value"]["preco_pago"] == 21.0
    # Transições disponíveis a partir de 'open'.
    transitions = {t["key"] for t in detail["available_transitions"]}
    assert transitions == {"in_analysis", "escalated", "accepted_fp", "blocked"}
    assert detail["is_terminal"] is False


@pytest.mark.asyncio
async def test_transition_finding_valid_path():
    """Sequência open → in_analysis → accepted_fp aplicada e refletida."""
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    inbox = await svc.inbox_payload(rule_code="REGRA_LPU")
    finding_id = inbox["findings"]["rows"][0]["id"]

    user_id = await _create_test_user_id()
    ok, err = await svc.transition_finding(
        finding_id, new_status="in_analysis",
        decision_reason="investigando", decided_by_user_id=user_id,
    )
    assert ok and err is None

    refreshed = await svc.finding_detail(finding_id)
    assert refreshed["status"] == "in_analysis"
    assert refreshed["status_label"] == "Em Análise"
    assert refreshed["decision_reason"] == "investigando"
    assert refreshed["decided_at"] is not None

    # Próxima transição válida.
    ok2, _ = await svc.transition_finding(
        finding_id, new_status="accepted_fp",
        decision_reason="falso positivo confirmado",
        decided_by_user_id=user_id,
    )
    assert ok2
    terminal = await svc.finding_detail(finding_id)
    assert terminal["status"] == "accepted_fp"
    assert terminal["is_terminal"] is True
    assert terminal["available_transitions"] == []


@pytest.mark.asyncio
async def test_transition_finding_invalid_transition_rejected():
    """open → blocked direto é permitido; mas accepted_fp → open não (terminal)."""
    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    inbox = await svc.inbox_payload(rule_code="REGRA_LPU")
    finding_id = inbox["findings"]["rows"][0]["id"]
    user_id = await _create_test_user_id()

    # Direto pra terminal
    ok, _ = await svc.transition_finding(
        finding_id, new_status="accepted_fp",
        decision_reason=None, decided_by_user_id=user_id,
    )
    assert ok
    # Tentar sair do terminal — rejeitado
    ok2, err = await svc.transition_finding(
        finding_id, new_status="open",
        decision_reason=None, decided_by_user_id=user_id,
    )
    assert not ok2
    assert err and "transição inválida" in err


@pytest.mark.asyncio
async def test_transition_finding_unknown_id():
    svc = PaymentsDashboardService()
    ok, err = await svc.transition_finding(
        "00000000-0000-0000-0000-000000000000", new_status="in_analysis",
        decision_reason=None, decided_by_user_id=None,
    )
    assert not ok
    assert err == "finding não encontrado"


@pytest.mark.asyncio
async def test_alerta_detalhe_route_renders():
    """Rota GET /alertas/{id}: 200, breadcrumb, formulário decisão visível."""
    from app.main import app

    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    inbox = await svc.inbox_payload(rule_code="REGRA_LPU")
    finding_id = inbox["findings"]["rows"][0]["id"]

    await _create_user("ctrl_detalhe", "senha-teste-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_detalhe", "senha-teste-123")
        resp = await client.get(f"/payments/empreiteiras-wf/alertas/{finding_id}")
    assert resp.status_code == 200
    body = resp.text
    # Breadcrumb completo
    assert 'href="/payments/empreiteiras-wf/alertas"' in body
    # Dados do finding
    assert "ENGEMAN MNT" in body
    assert "REGRA_LPU" in body
    assert "R$ 1.000,00" in body
    # Formulário decisão visível (não terminal)
    assert 'action="/payments/empreiteiras-wf/alertas/' in body
    assert 'name="new_status"' in body


@pytest.mark.asyncio
async def test_alerta_detalhe_route_404_for_unknown():
    from app.main import app

    await _create_user("ctrl_404", "senha-teste-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_404", "senha-teste-123")
        resp = await client.get(
            "/payments/empreiteiras-wf/alertas/00000000-0000-0000-0000-000000000000"
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_alerta_decide_route_applies_transition_and_redirects():
    """POST /decide: aplica transição, redireciona pro detalhe (303), e ao
    seguir o redirect mostra o status atualizado."""
    from app.main import app

    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    inbox = await svc.inbox_payload(rule_code="REGRA_LPU")
    finding_id = inbox["findings"]["rows"][0]["id"]

    await _create_user("ctrl_decide", "senha-teste-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_decide", "senha-teste-123")
        resp = await client.post(
            f"/payments/empreiteiras-wf/alertas/{finding_id}/decide",
            data={"new_status": "in_analysis", "decision_reason": "vou olhar"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert finding_id in resp.headers["location"]

        # Segue o redirect; status agora é Em Análise.
        resp2 = await client.get(resp.headers["location"])
        assert resp2.status_code == 200
        assert "Em Análise" in resp2.text
        assert "vou olhar" in resp2.text


@pytest.mark.asyncio
async def test_alerta_decide_route_rejects_invalid_transition():
    """POST /decide com transição inválida → 400."""
    from app.main import app

    await _seed_minimal_dataset()
    svc = PaymentsDashboardService()
    inbox = await svc.inbox_payload(rule_code="REGRA_LPU")
    finding_id = inbox["findings"]["rows"][0]["id"]
    # Move pra accepted_fp (terminal) primeiro
    user_id = await _create_test_user_id()
    await svc.transition_finding(
        finding_id, new_status="accepted_fp",
        decision_reason=None, decided_by_user_id=user_id,
    )

    await _create_user("ctrl_400", "senha-teste-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl_400", "senha-teste-123")
        resp = await client.post(
            f"/payments/empreiteiras-wf/alertas/{finding_id}/decide",
            data={"new_status": "open"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_alertas_route_requires_role():
    """analista_n3 → 403; sem auth → redirect."""
    from app.main import app

    await _create_user("n3_alertas", "senha-teste-123", roles=["analista_n3"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Sem login
        resp = await client.get("/payments/empreiteiras-wf/alertas", follow_redirects=False)
        assert resp.status_code in (302, 307)
        # Login com role insuficiente
        await _login_as(client, "n3_alertas", "senha-teste-123")
        resp = await client.get("/payments/empreiteiras-wf/alertas")
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_dashboard_page_carries_active_filters_into_inputs():
    """Filtros aplicados na URL aparecem como `value` no form (state-aware)."""
    from app.main import app

    await _seed_minimal_dataset()
    await _create_user("ctrl4", "senha-teste-123", roles=["controladoria"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login_as(client, "ctrl4", "senha-teste-123")
        resp = await client.get(
            "/payments/empreiteiras-wf",
            params={"search": "EQS", "tipo": "Alerta Proc."},
        )
    assert resp.status_code == 200
    body = resp.text
    # Input search renderiza value="EQS".
    assert 'value="EQS"' in body
    # Tipo aparece selected.
    assert '<option value="Alerta Proc." selected>' in body

"""Tests dos 3 detectores temporais (Bloco C):

  - R7_VALIDADE_VENCIDA
  - R7_PICO_FIM_PERIODO
  - R7_PERIODOS_ATIPICOS
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.adapters.db.postgres import init_db
from app.adapters.db.postgres_payments import connect_payments, init_payments_schema
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.domain.payments import AnalyticDetector, Severity, Technique
from app.core.services.auth_service import AuthService
from app.core.services.payments.analytics import (
    ANALYTICS_REGISTRY,
    AnalyticContext,
)
from app.core.services.payments.analytics import _register_all  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_detector(
    code: str,
    technique: Technique,
    threshold: dict | None = None,
    severity: Severity = Severity.MEDIUM,
) -> AnalyticDetector:
    return AnalyticDetector(
        code=code,
        name=code,
        description="test",
        technique=technique,
        severity=severity,
        threshold_params=threshold or {},
        python_handler=f"app.core.services.payments.analytics.{code.lower()}",
    )


async def _create_user_id() -> str:
    await init_db()
    auth = AuthService(PgUserRepository())
    user = await auth.register(
        username=f"seed_temp_{uuid4().hex[:6]}",
        password="seed-pass-123",
        roles=["admin"],
    )
    return str(user.id)


async def _seed_contract_with_validity(
    *, empreiteira: str, cnpj: str, valid_from: date, valid_to: date
) -> tuple[str, str]:
    """Cria supplier_bridge + contract_master + contract_version (current).
    Devolve (contract_master_id, supplier_bridge_id)."""
    user_id = await _create_user_id()
    sup_id = str(uuid4())
    cm_id = str(uuid4())
    cv_id = str(uuid4())
    async with connect_payments() as c:
        await c.execute(
            """
            INSERT INTO payments.supplier_bridge
                (id, categoria, empreiteira, contrato_num_sap, ref_ws,
                 numero_fornecedor_sap, cnpj)
            VALUES ($1, 'INSTALACAO', $2, $3, $4, $5, $6)
            """,
            sup_id, empreiteira, f"4600{sup_id[:6]}", f"WS{sup_id[:6]}",
            f"100{sup_id[:6]}", cnpj,
        )
        await c.execute(
            """
            INSERT INTO payments.contract_master
                (id, supplier_bridge_id, contrato_num_sap, ref_ws, cnpj,
                 is_monitored, created_by_id)
            VALUES ($1, $2, $3, $4, $5, TRUE, $6::uuid)
            """,
            cm_id, sup_id, f"4600{sup_id[:6]}", f"WS{sup_id[:6]}", cnpj, user_id,
        )
        await c.execute(
            """
            INSERT INTO payments.contract_version
                (id, contract_master_id, version_number, valid_from, valid_to)
            VALUES ($1, $2, 1, $3, $4)
            """,
            cv_id, cm_id, valid_from, valid_to,
        )
        await c.execute(
            "UPDATE payments.contract_master SET current_version_id = $1 WHERE id = $2",
            cv_id, cm_id,
        )
    return cm_id, sup_id


async def _bulk_insert_payments(rows: list[dict]) -> None:
    if not rows:
        return
    async with connect_payments() as c:
        await c.executemany(
            """
            INSERT INTO payments.wf_payment (
                os_num, sistema, empreiteira, data_pedido, data_execucao,
                valor_unitario, valor_total_final, material_servico_num,
                mes_medicao, status_os, nivel_gerencial, malogro
            ) VALUES (
                $1, 'WF1', $2, $3, $4, $5, $6, $7, $8,
                'EXECUTADO', 'Em Pagamento', 'OK'
            )
            """,
            [
                (
                    r["os_num"], r["empreiteira"], r["data_pedido"],
                    r.get("data_execucao"),
                    r.get("valor_unitario"), r.get("valor_total_final"),
                    r.get("material_servico_num"),
                    r.get("mes_medicao"),
                )
                for r in rows
            ],
        )


@pytest.fixture
async def _payments_schema():
    await init_payments_schema()


# ---------------------------------------------------------------------------
# R7_VALIDADE_VENCIDA
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_validade_vencida_empty_db(_payments_schema):
    detector = _make_detector("R7_VALIDADE_VENCIDA", Technique.HEURISTIC)
    drafts = [
        d async for d in ANALYTICS_REGISTRY["R7_VALIDADE_VENCIDA"](
            AnalyticContext(detector=detector)
        )
    ]
    assert drafts == []


@pytest.mark.asyncio
async def test_r7_validade_vencida_flags_payments_after_valid_to(_payments_schema):
    """Contract valid_to=2025-01-31. 1 payment em fevereiro/2025 = vencido."""
    await _seed_contract_with_validity(
        empreiteira="EMP_VENC", cnpj="11122233000111",
        valid_from=date(2024, 1, 1), valid_to=date(2025, 1, 31),
    )
    await _bulk_insert_payments([
        {  # Within validity — não deve flagar
            "os_num": "OS-OK", "empreiteira": "EMP_VENC",
            "data_pedido": date(2025, 1, 15),
            "valor_unitario": Decimal("10"), "valor_total_final": Decimal("100"),
        },
        {  # 10 days after valid_to — flag
            "os_num": "OS-LATE", "empreiteira": "EMP_VENC",
            "data_pedido": date(2025, 2, 10),
            "valor_unitario": Decimal("10"), "valor_total_final": Decimal("500"),
        },
    ])

    detector = _make_detector("R7_VALIDADE_VENCIDA", Technique.HEURISTIC)
    drafts = [
        d async for d in ANALYTICS_REGISTRY["R7_VALIDADE_VENCIDA"](
            AnalyticContext(detector=detector)
        )
    ]
    assert len(drafts) == 1
    d = drafts[0]
    assert d.actual_value["os_num"] == "OS-LATE"
    assert d.actual_value["days_over"] == 10
    assert d.score == 10.0


# ---------------------------------------------------------------------------
# R7_PICO_FIM_PERIODO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_pico_fim_periodo_empty_db(_payments_schema):
    detector = _make_detector(
        "R7_PICO_FIM_PERIODO", Technique.TIMESERIES_OUTLIER,
        {"last_n_days": 30, "spike_threshold": 2.0},
    )
    drafts = [
        d async for d in ANALYTICS_REGISTRY["R7_PICO_FIM_PERIODO"](
            AnalyticContext(detector=detector)
        )
    ]
    assert drafts == []


@pytest.mark.asyncio
async def test_r7_pico_fim_periodo_detects_end_of_contract_spike(_payments_schema):
    """Contract jan-dez/2025. Pagamentos baixos jan-out (~R$ 100/mês), pico
    nos últimos 30 dias (~R$ 10k em dez)."""
    await _seed_contract_with_validity(
        empreiteira="EMP_SPIKE", cnpj="22233344000122",
        valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31),
    )
    rows = []
    # 10 meses de pagamentos pequenos.
    for mes in range(1, 11):
        rows.append({
            "os_num": f"OS-EARLY-{mes}", "empreiteira": "EMP_SPIKE",
            "data_pedido": date(2025, mes, 15),
            "valor_unitario": Decimal("10"), "valor_total_final": Decimal("100"),
        })
    # 10 pagamentos enormes nos últimos 30 dias.
    for i in range(10):
        rows.append({
            "os_num": f"OS-SPIKE-{i}", "empreiteira": "EMP_SPIKE",
            "data_pedido": date(2025, 12, 10 + (i % 20)),
            "valor_unitario": Decimal("1000"), "valor_total_final": Decimal("10000"),
        })
    await _bulk_insert_payments(rows)

    detector = _make_detector(
        "R7_PICO_FIM_PERIODO", Technique.TIMESERIES_OUTLIER,
        {"last_n_days": 30, "spike_threshold": 2.0},
    )
    drafts = [
        d async for d in ANALYTICS_REGISTRY["R7_PICO_FIM_PERIODO"](
            AnalyticContext(detector=detector)
        )
    ]
    assert len(drafts) == 1
    d = drafts[0]
    assert d.actual_value["empreiteira"] == "EMP_SPIKE"
    assert d.score > 2.0
    # Evidence inclui os payments do fim.
    assert len(d.evidence_payment_ids) >= 5


# ---------------------------------------------------------------------------
# R7_PERIODOS_ATIPICOS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_periodos_atipicos_empty_db(_payments_schema):
    detector = _make_detector(
        "R7_PERIODOS_ATIPICOS", Technique.TIMESERIES_OUTLIER,
    )
    drafts = [
        d async for d in ANALYTICS_REGISTRY["R7_PERIODOS_ATIPICOS"](
            AnalyticContext(detector=detector)
        )
    ]
    assert drafts == []


@pytest.mark.asyncio
async def test_r7_periodos_atipicos_detects_concentrated_month(_payments_schema):
    """Empreiteira com 12 meses ≈ R$ 1k cada, exceto dezembro com R$ 50k."""
    rows = []
    for mes in range(1, 12):
        rows.append({
            "os_num": f"OS-{mes:02d}", "empreiteira": "EMP_PER",
            "data_pedido": date(2025, mes, 15),
            "valor_unitario": Decimal("100"), "valor_total_final": Decimal("1000"),
        })
    # Dezembro — pico.
    rows.append({
        "os_num": "OS-DEZ", "empreiteira": "EMP_PER",
        "data_pedido": date(2025, 12, 20),
        "valor_unitario": Decimal("5000"), "valor_total_final": Decimal("50000"),
    })
    await _bulk_insert_payments(rows)

    detector = _make_detector(
        "R7_PERIODOS_ATIPICOS", Technique.TIMESERIES_OUTLIER,
        {"zscore_threshold": 2.0, "min_months_distinct": 6},
    )
    drafts = [
        d async for d in ANALYTICS_REGISTRY["R7_PERIODOS_ATIPICOS"](
            AnalyticContext(detector=detector)
        )
    ]
    # Pelo menos o dezembro flagado.
    dec = next(
        (d for d in drafts if d.actual_value["mes_calendario"] == 12), None
    )
    assert dec is not None
    assert dec.score > 2.0
    assert dec.actual_value["empreiteira"] == "EMP_PER"

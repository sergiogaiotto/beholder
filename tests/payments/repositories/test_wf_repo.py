"""Integration tests do PgWFPaymentRepository (com filtro universal SDD §9)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.adapters.db.repositories.payments import PgWFPaymentRepository
from app.core.domain.payments import Sistema, TipoDespesa, WFPayment


def _wf(**overrides) -> WFPayment:
    """WFPayment com defaults que PASSAM no filtro universal."""
    base = dict(
        os_num="OS-001",
        data_pedido=date(2025, 6, 1),
        sistema=Sistema.WF1,
        valor_total_final=Decimal("1000.00"),
        empreiteira="ABILITY",
        status_os="EXECUTADO",
        nivel_gerencial="Em Pagamento",
        malogro="OK",
    )
    base.update(overrides)
    return WFPayment(**base)


async def test_bulk_insert_e_count(ingestion_run_id):
    repo = PgWFPaymentRepository()
    items = [
        _wf(os_num=f"OS-{i:03}", ingestion_run_id=ingestion_run_id)
        for i in range(5)
    ]
    n = await repo.bulk_insert(items)
    assert n == 5
    assert await repo.count_total() == 5


async def test_list_universe_filters_status_os(ingestion_run_id):
    """status_os fora do allowed → não entra no universe."""
    repo = PgWFPaymentRepository()
    await repo.bulk_insert([
        _wf(os_num="IN-1", ingestion_run_id=ingestion_run_id),  # OK
        _wf(os_num="OUT-1", status_os="CANCELADO", ingestion_run_id=ingestion_run_id),  # ❌
    ])

    universe = await repo.list_universe(
        since=date(2025, 1, 1), until=date(2026, 1, 1)
    )
    assert len(universe) == 1
    assert universe[0].os_num == "IN-1"


async def test_list_universe_filters_nivel_gerencial(ingestion_run_id):
    repo = PgWFPaymentRepository()
    await repo.bulk_insert([
        _wf(os_num="IN-1", ingestion_run_id=ingestion_run_id),  # OK
        _wf(os_num="OUT-1", nivel_gerencial="Cancelado", ingestion_run_id=ingestion_run_id),  # ❌
    ])

    universe = await repo.list_universe(
        since=date(2025, 1, 1), until=date(2026, 1, 1)
    )
    assert {p.os_num for p in universe} == {"IN-1"}


async def test_list_universe_filters_malogro_error(ingestion_run_id):
    repo = PgWFPaymentRepository()
    await repo.bulk_insert([
        _wf(os_num="IN-1", malogro="OK", ingestion_run_id=ingestion_run_id),
        _wf(os_num="OUT-1", malogro="ERROR", ingestion_run_id=ingestion_run_id),
    ])

    universe = await repo.list_universe(
        since=date(2025, 1, 1), until=date(2026, 1, 1)
    )
    assert {p.os_num for p in universe} == {"IN-1"}


async def test_list_universe_filters_by_empreiteira(ingestion_run_id):
    repo = PgWFPaymentRepository()
    await repo.bulk_insert([
        _wf(os_num="A-1", empreiteira="ABILITY", ingestion_run_id=ingestion_run_id),
        _wf(os_num="B-1", empreiteira="BETA", ingestion_run_id=ingestion_run_id),
    ])

    only_ability = await repo.list_universe(
        since=date(2025, 1, 1), until=date(2026, 1, 1), empreiteira="ABILITY"
    )
    assert {p.os_num for p in only_ability} == {"A-1"}


async def test_count_universe_e_count_total_diferem(ingestion_run_id):
    repo = PgWFPaymentRepository()
    await repo.bulk_insert([
        _wf(os_num="A", ingestion_run_id=ingestion_run_id),  # IN
        _wf(os_num="B", status_os="CANCELADO", ingestion_run_id=ingestion_run_id),  # OUT
        _wf(os_num="C", malogro="ERROR", ingestion_run_id=ingestion_run_id),  # OUT
    ])
    assert await repo.count_total() == 3
    assert await repo.count_universe(
        since=date(2025, 1, 1), until=date(2026, 1, 1)
    ) == 1


async def test_persists_e_recupera_taxonomia_completa(ingestion_run_id):
    """Garante que sistema/tipo_de_despesa enum roundtrip via .value."""
    repo = PgWFPaymentRepository()
    item = _wf(
        os_num="OS-FULL",
        sistema=Sistema.WF2,
        tipo_de_despesa=TipoDespesa.CAPEX,
        uf="RJ",
        mes_medicao="2025/06",
        ingestion_run_id=ingestion_run_id,
    )
    await repo.bulk_insert([item])

    universe = await repo.list_universe(
        since=date(2025, 1, 1), until=date(2026, 1, 1)
    )
    assert len(universe) == 1
    fetched = universe[0]
    assert fetched.sistema is Sistema.WF2
    assert fetched.tipo_de_despesa is TipoDespesa.CAPEX
    assert fetched.uf == "RJ"
    assert fetched.mes_medicao == "2025/06"

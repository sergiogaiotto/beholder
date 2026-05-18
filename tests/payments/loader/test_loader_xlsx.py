"""Tests integrados do loader contra XLSX fixtures + DB real.

Cada test simula 1 carga end-to-end: arquivo → projeção → DB →
verificação via repo direto.
"""

from __future__ import annotations

from app.adapters.db.repositories.payments import (
    PgCostCenterAccountRepository,
    PgIngestionRunRepository,
    PgSupplierBridgeRepository,
    PgWFPaymentRepository,
)
from app.core.domain.payments import IngestionStatus
from app.core.services.payments.ingestion import load_source_by_path


async def test_supplier_bridge_xlsx_load(supplier_bridge_xlsx):
    """XLSX → SupplierBridge via bulk_upsert; rastreabilidade via IngestionRun."""
    result = await load_source_by_path(supplier_bridge_xlsx, "supplier_bridge")

    assert result.rows_read == 2
    assert result.rows_inserted == 2
    assert result.rows_failed == 0

    # Verifica persistência no DB
    sb_repo = PgSupplierBridgeRepository()
    assert await sb_repo.count() == 2
    ability = await sb_repo.get_by_contrato("4600012345")
    assert ability is not None
    assert ability.empreiteira == "ABILITY"
    assert ability.cnpj == "12345678000199"

    # IngestionRun ficou marcado completed
    ir_repo = PgIngestionRunRepository()
    run = await ir_repo.get(result.run.id)
    assert run.status is IngestionStatus.COMPLETED
    assert run.rows_read == 2
    assert run.rows_inserted == 2
    assert run.finished_at is not None
    assert run.target_table == "payments.supplier_bridge"


async def test_supplier_bridge_bulk_upsert_is_idempotent(supplier_bridge_xlsx):
    """Carregar 2x o mesmo arquivo não duplica rows (bulk_upsert por (contrato,ref))."""
    sb_repo = PgSupplierBridgeRepository()

    await load_source_by_path(supplier_bridge_xlsx, "supplier_bridge")
    assert await sb_repo.count() == 2

    await load_source_by_path(supplier_bridge_xlsx, "supplier_bridge")
    assert await sb_repo.count() == 2


async def test_cost_center_xlsx_load(cost_center_xlsx):
    """XLSX CC+CONTA → CostCenterAccount via bulk_upsert (3 rows)."""
    result = await load_source_by_path(cost_center_xlsx, "cost_center")
    assert result.rows_read == 3

    cca_repo = PgCostCenterAccountRepository()
    assert await cca_repo.count() == 3
    contas_cc1 = await cca_repo.get_contas_for_cc("CC-1")
    assert contas_cc1 == ["6010101", "6010102"]


async def test_wf_payment_xlsx_load(wf_payment_xlsx):
    """XLSX WF analítico → WFPayment + filtro universal funciona pós-carga."""
    from datetime import date

    result = await load_source_by_path(wf_payment_xlsx, "wf_payment")
    assert result.rows_read == 3
    assert result.rows_inserted == 3

    wf_repo = PgWFPaymentRepository()
    assert await wf_repo.count_total() == 3

    # Filtro universal SDD §9: status_os ∈ executado/em execução
    # → OS-3 (CANCELADO) fica de fora; OS-1 e OS-2 entram.
    universe_count = await wf_repo.count_universe(
        since=date(2025, 1, 1), until=date(2027, 1, 1)
    )
    assert universe_count == 2


async def test_ingestion_run_id_is_injected_into_wf_rows(wf_payment_xlsx):
    """Cada WFPayment carregado deve ter ingestion_run_id == run.id."""
    from datetime import date

    result = await load_source_by_path(wf_payment_xlsx, "wf_payment")

    wf_repo = PgWFPaymentRepository()
    rows = await wf_repo.list_universe(
        since=date(2025, 1, 1), until=date(2027, 1, 1), limit=10
    )
    assert len(rows) == 2
    for r in rows:
        assert r.ingestion_run_id == result.run.id


async def test_ingestion_run_lifecycle_transitions(supplier_bridge_xlsx):
    """status: pending → (não capturável aqui pq ocorre dentro do load) → completed.

    Verificamos status final + finished_at + rows_inserted contagem.
    """
    result = await load_source_by_path(supplier_bridge_xlsx, "supplier_bridge")

    ir_repo = PgIngestionRunRepository()
    run = await ir_repo.get(result.run.id)
    assert run.status is IngestionStatus.COMPLETED
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.finished_at >= run.started_at
    assert run.rows_read == 2
    assert run.rows_inserted == 2


async def test_loader_marks_failed_on_required_field_missing(tmp_path):
    """Source sem field required+raise (OS) → mark_failed e re-raise.

    Nota: data_pedido também é required mas tem on_missing=skip_row (Pré-B
    confirmou 14% null). OS é required + on_missing=raise (default).
    """
    from datetime import datetime as _dt

    import pytest
    from openpyxl import Workbook

    from app.adapters.sap.parsers.wf_analytics import (
        WF_ANALYTICS_EXPECTED_HEADERS,
    )

    p = tmp_path / "wf_bad.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Analitico_Empreiteiras_WF1_WF2_"
    ws.append(list(WF_ANALYTICS_EXPECTED_HEADERS))
    headers = list(WF_ANALYTICS_EXPECTED_HEADERS)
    row = [None] * len(headers)
    row[headers.index("SISTEMA")] = "WF1"
    row[headers.index("DATA_PEDIDO")] = _dt(2025, 6, 1)
    # OS deixado None — vai falhar com on_missing=raise (default)
    ws.append(row)
    wb.save(str(p))

    with pytest.raises(ValueError, match="os_num"):
        await load_source_by_path(p, "wf_payment")

    # IngestionRun precisa ter sido marcado failed antes da exception subir
    ir_repo = PgIngestionRunRepository()
    recent = await ir_repo.list_recent(limit=5)
    failed_runs = [r for r in recent if r.status is IngestionStatus.FAILED]
    assert len(failed_runs) >= 1
    assert "os_num" in failed_runs[0].error_message

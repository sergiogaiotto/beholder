"""Tests do parser WF Analítico (XLSX 81 cols)."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.adapters.sap.parsers.wf_analytics import (
    WF_ANALYTICS_DEFAULT_SHEET,
    WF_ANALYTICS_EXPECTED_HEADERS,
    iter_wf_analytics_rows,
    parse_wf_analytics,
)


def test_expected_headers_has_81_entries():
    """Pré-B §3: 81 cols oficiais."""
    assert len(WF_ANALYTICS_EXPECTED_HEADERS) == 81


def test_default_sheet_name_matches_pre_b():
    assert WF_ANALYTICS_DEFAULT_SHEET == "Analitico_Empreiteiras_WF1_WF2_"


def test_iter_yields_2_sample_rows(wf_analytics_sample_path):
    rows = list(iter_wf_analytics_rows(wf_analytics_sample_path))
    assert len(rows) == 2


def test_first_row_has_expected_values(wf_analytics_sample_path):
    rows = list(iter_wf_analytics_rows(wf_analytics_sample_path))
    r = rows[0]
    assert r["SISTEMA"] == "WF1"
    assert r["OS"] == "OS-001"
    assert r["EMPREITEIRA"] == "ABILITY"
    assert r["CONTRATO_NUM"] == "4600012345"
    assert r["UF"] == "RJ"
    assert r["STATUS_OS"] == "EXECUTADO"
    assert r["NIVEL_GERENCIAL"] == "Em Pagamento"
    assert r["DATA_PEDIDO"] == datetime(2025, 6, 1)
    assert r["VALOR_TOTAL_FINAL"] == 1500.00
    assert r["MES_MEDICAO"] == "2025/06"


def test_unfilled_cells_are_none(wf_analytics_sample_path):
    rows = list(iter_wf_analytics_rows(wf_analytics_sample_path))
    # Row 1 não preencheu CATEGORIA
    assert rows[0]["CATEGORIA"] is None
    # Row 2 só preencheu poucas — outras devem ser None
    assert rows[1]["EMPREITEIRA"] == "BETA"
    assert rows[1]["VALOR_TOTAL_FINAL"] is None


def test_parse_wf_analytics_is_alias_of_iter(wf_analytics_sample_path):
    """parse_ é alias semântico de iter_; mesmo output."""
    from_iter = list(iter_wf_analytics_rows(wf_analytics_sample_path))
    from_parse = list(parse_wf_analytics(wf_analytics_sample_path))
    assert from_iter == from_parse


def test_schema_validation_accepts_extra_cols(wf_extra_cols_xlsx):
    """Cols além das 81 oficiais são forward-compat — não bloqueia."""
    rows = list(iter_wf_analytics_rows(wf_extra_cols_xlsx))
    assert len(rows) == 1
    # Col extra é incluída no dict (não filtramos no parser)
    assert "NOVO_CAMPO_FUTURO" in rows[0]
    assert rows[0]["NOVO_CAMPO_FUTURO"] == "extra"


def test_schema_validation_rejects_missing_cols(wf_missing_cols_xlsx):
    """Cols oficiais ausentes (ex: SISTEMA faltando) = ValueError."""
    with pytest.raises(ValueError, match="SISTEMA"):
        list(iter_wf_analytics_rows(wf_missing_cols_xlsx))


def test_validate_schema_false_skips_validation(wf_missing_cols_xlsx):
    """validate_schema=False permite ler mesmo com cols faltando."""
    rows = list(
        iter_wf_analytics_rows(
            wf_missing_cols_xlsx, validate_schema=False
        )
    )
    # Não levanta — só lê
    assert len(rows) == 1

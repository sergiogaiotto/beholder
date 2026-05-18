"""Tests do parser MSRV5."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.adapters.sap.parsers.msrv5 import (
    MSRV5_COLUMNS,
    iter_msrv5_rows,
    parse_msrv5,
)


def test_columns_match_schema():
    """Schema fixo Pré-B §2.2."""
    assert MSRV5_COLUMNS == (
        "data_documento", "documento_compras", "item", "numero_servico",
        "qtd_solicitada", "preco_unitario", "texto_breve",
    )


def test_iter_yields_only_valid_rows(msrv5_sample_path):
    """Conftest tem 3 rows válidas + ruído (header, separador, footer,
    malformed, data inválida). iter retorna só as 3 válidas + 1 com
    data inválida (parse_msrv5 filtra; iter retorna raw)."""
    rows = list(iter_msrv5_rows(msrv5_sample_path))
    # 3 válidas + 1 com data inválida (passa filtro de iter, falha em parse)
    assert len(rows) == 4
    assert all(len(r) == 7 for r in rows)
    # Primeira válida
    assert rows[0][0] == "06.09.2022"
    assert rows[0][1] == "5700012782"
    assert rows[0][2] == "7913"


def test_iter_skips_separators_and_headers(msrv5_sample_path):
    """Confirma que --- e header 'Data doc.' não aparecem."""
    rows = list(iter_msrv5_rows(msrv5_sample_path))
    for r in rows:
        assert r[0] != "Data doc."
        assert not r[0].startswith("-")


def test_iter_skips_footer(msrv5_sample_path):
    """Footer 'Estat: ...' (sem |) não aparece."""
    rows = list(iter_msrv5_rows(msrv5_sample_path))
    for r in rows:
        assert "Estat" not in str(r[0])


def test_parse_msrv5_returns_typed_dicts(msrv5_sample_path):
    """parse_msrv5 retorna dicts tipados; skipa row com data inválida."""
    rows = list(parse_msrv5(msrv5_sample_path))
    # 3 válidas (data inválida foi skipada)
    assert len(rows) == 3

    r = rows[0]
    assert r["data_documento"] == date(2022, 9, 6)
    assert r["documento_compras"] == "5700012782"
    assert r["item"] == 7913
    assert r["numero_servico"] == "9000507"
    assert r["qtd_solicitada"] == Decimal("0.000")
    assert r["preco_unitario"] == Decimal("2.71")
    assert r["texto_breve"] == "SERV CONFECCAO MAT"


def test_parse_msrv5_skips_invalid_date(msrv5_sample_path):
    """Row com '31.02.2024' (fevereiro não tem 31) deve ser skipada."""
    rows = list(parse_msrv5(msrv5_sample_path))
    dates = {r["data_documento"] for r in rows}
    assert date(2024, 2, 28) not in dates  # nunca chegou a parsear
    # Datas que devem estar presentes:
    assert date(2022, 9, 6) in dates
    assert date(2023, 5, 15) in dates
    assert date(2024, 1, 1) in dates


def test_parse_msrv5_handles_decimal_quantities(msrv5_sample_path):
    """qtd 0,000 e 1,500 devem virar Decimal corretamente."""
    rows = list(parse_msrv5(msrv5_sample_path))
    qtds = [r["qtd_solicitada"] for r in rows]
    assert Decimal("0") in qtds
    assert Decimal("1.500") in qtds


def test_parse_msrv5_iterator_is_lazy(msrv5_sample_path):
    """parse_msrv5 é generator — chama next() vez por vez."""
    gen = parse_msrv5(msrv5_sample_path)
    first = next(gen)
    assert first["documento_compras"] == "5700012782"
    second = next(gen)
    assert second["documento_compras"] == "5700099999"

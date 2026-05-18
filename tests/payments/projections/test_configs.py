"""Tests dos 7 YAMLs reais em configs/ — schema válido + smoke projection."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.adapters.sap.projections import (
    list_projections,
    load_projection,
    project,
)


EXPECTED_CONFIGS = {
    "supplier_bridge": "SupplierBridge",
    "ekko": "PurchaseOrderHeader",
    "ekpo": "PurchaseOrderItem",
    "esll": "ServicePackage",
    "gc": "PurchaseOrderGc",
    "wf_payment": "WFPayment",
    "cost_center": "CostCenterAccount",
}


def test_all_7_configs_present():
    found = list_projections()
    assert set(found.keys()) == set(EXPECTED_CONFIGS.keys())


def test_all_configs_load_without_error():
    for name, path in list_projections().items():
        cfg = load_projection(path)
        assert cfg.target_entity == EXPECTED_CONFIGS[name], (
            f"{name}: target_entity esperado {EXPECTED_CONFIGS[name]!r}, "
            f"got {cfg.target_entity!r}"
        )


@pytest.fixture
def configs() -> dict:
    return {name: load_projection(p) for name, p in list_projections().items()}


# ---------- Smoke projection contra fixtures sintéticas ----------


def test_supplier_bridge_smoke(configs):
    cfg = configs["supplier_bridge"]
    rows = [{
        "CATEGORIA": "OBRAS CIVIS",
        "EMPREITEIRA": "ABILITY",
        "CONTRATO_NUM": "4600012345",
        "REF WS": "WS-001",
        "NUMERO_FORNECEDOR SAP": "100200",
        "CNPJ": "12345678000199",
    }]
    result = list(project(cfg, iter(rows)))
    assert len(result) == 1
    assert result[0].empreiteira == "ABILITY"
    assert result[0].cnpj == "12345678000199"


def test_ekko_smoke_with_catchall(configs):
    cfg = configs["ekko"]
    rows = [{
        "Documento de compras": "4500000001",
        "Empresa": "0001",
        "Ctg.doc.compras": "F",
        "Fornecedor": "100200",
        "Data do documento": datetime(2024, 6, 1),
        "Início per.validade": datetime(2024, 1, 1),
        "Fim da validade": datetime(2025, 12, 31),
        "ValFix.(nível cab.)": "1500000,00",
        "Moeda": "BRL",
        "Status": "ATIVO",
        "Comprador adicional": "JOÃO",  # vai pro raw_extra
    }]
    result = list(project(cfg, iter(rows)))
    assert len(result) == 1
    poh = result[0]
    assert poh.documento_compras == "4500000001"
    assert poh.empresa == "0001"
    assert poh.fornecedor == "100200"
    assert poh.data_documento == date(2024, 6, 1)
    assert poh.val_fix_cab == Decimal("1500000.00")
    assert poh.raw_extra == {"Comprador adicional": "JOÃO"}


def test_ekpo_smoke(configs):
    cfg = configs["ekpo"]
    rows = [{
        "Documento de compras": "4500000001",
        "Item": "00010",
        "Texto breve": "INSTALACAO DE FIBRA",
        "Qtd.do pedido": "100,000",
        "UM pedido": "UN",
        "Preço líq.pedido": "125,50",
        "Valor líquido pedido": "12550,00",
    }]
    result = list(project(cfg, iter(rows)))
    poi = result[0]
    assert poi.documento_compras == "4500000001"
    assert poi.item == "00010"
    assert poi.quantidade == Decimal("100.000")
    assert poi.valor_liquido == Decimal("12550.00")


def test_esll_smoke(configs):
    cfg = configs["esll"]
    rows = [{
        "Nº pacote": "0000000001",
        "Linha": 1,
        "Nº de serviço": "9000507",
        "Texto breve": "SERV CONFECCAO",
        "Preço bruto": "125,50",
        "Qtd.solicitada": "1,500",
    }]
    result = list(project(cfg, iter(rows)))
    sp = result[0]
    assert sp.pacote == "0000000001"
    assert sp.linha == 1
    assert sp.numero_servico == "9000507"
    assert sp.preco_bruto == Decimal("125.50")


def test_gc_smoke(configs):
    cfg = configs["gc"]
    rows = [{
        "Documento de compras": "4600012345",
        "Item": "00010",
        "Empresa": "0001",
        "Nº de serviço": "9000507",
        "Preço bruto (LPU)": "2,71",
    }]
    result = list(project(cfg, iter(rows)))
    gc = result[0]
    assert gc.documento_compras == "4600012345"
    assert gc.item == "00010"
    assert gc.preco_bruto_lpu == Decimal("2.71")
    assert gc.numero_servico == "9000507"


def test_wf_payment_smoke(configs):
    cfg = configs["wf_payment"]
    from app.core.domain.payments import Sistema, TipoDespesa
    rows = [{
        "SISTEMA": "WF1",
        "OS": "OS-12345",
        "EMPREITEIRA": "ABILITY",
        "CONTRATO_NUM": "4600012345",
        "ITEM_NUM": "10",
        "UF": "RJ",
        "CIDADE": "Rio de Janeiro",
        "STATUS_OS": "EXECUTADO",
        "NIVEL_GERENCIAL": "Em Pagamento",
        "MALOGRO": "NAO",
        "TIPO_DE_DESPESA": "OPEX",
        "DATA_PEDIDO": datetime(2025, 6, 1),
        "VALOR_TOTAL_FINAL": "1500.00",
        "MES_MEDICAO": "2025/06",
        "VESTIGIAL_COL": "x",  # vai pro raw_extra
    }]
    result = list(project(cfg, iter(rows)))
    wf = result[0]
    assert wf.os_num == "OS-12345"
    assert wf.sistema is Sistema.WF1
    assert wf.tipo_de_despesa is TipoDespesa.OPEX
    assert wf.data_pedido == date(2025, 6, 1)
    assert wf.valor_total_final == Decimal("1500.00")
    assert wf.uf == "RJ"
    assert wf.raw_extra == {"VESTIGIAL_COL": "x"}


def test_cost_center_smoke(configs):
    cfg = configs["cost_center"]
    rows = [{
        "CENTRO_DE_CUSTO": "CC-1234",
        "CONTA_RAZAO": "6010101",
    }]
    result = list(project(cfg, iter(rows)))
    cca = result[0]
    assert cca.centro_de_custo == "CC-1234"
    assert cca.conta_razao == "6010101"


def test_wf_payment_required_data_pedido_raises_if_missing(configs):
    """data_pedido é required (partition key WF) — DT ausente quebra."""
    cfg = configs["wf_payment"]
    rows = [{"SISTEMA": "WF1", "OS": "OS-1"}]
    with pytest.raises(ValueError, match="data_pedido"):
        list(project(cfg, iter(rows)))

"""Tests das 5 entidades SAP."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.domain.payments.sap import (
    CostCenterAccount,
    PurchaseOrderGc,
    PurchaseOrderHeader,
    PurchaseOrderItem,
    ServicePackage,
)


# ---------- PurchaseOrderHeader ----------


def test_po_header_happy():
    po = PurchaseOrderHeader(
        documento_compras="4500098765",
        empresa="0001",
        categoria_doc="K",
        fornecedor="100200",
        contrato_basico="4600012345",
        data_documento=date(2024, 3, 1),
        val_fix_cab=Decimal("1500000.00"),
    )
    assert po.moeda == "BRL"
    assert po.raw_extra == {}


def test_po_header_requires_documento_e_empresa_e_fornecedor():
    with pytest.raises(ValidationError):
        PurchaseOrderHeader(empresa="0001", fornecedor="100200")


def test_po_header_rejects_negative_val_fix_cab():
    with pytest.raises(ValidationError):
        PurchaseOrderHeader(
            documento_compras="x",
            empresa="0001",
            fornecedor="100200",
            val_fix_cab=Decimal("-1"),
        )


# ---------- PurchaseOrderItem ----------


def test_po_item_happy():
    item = PurchaseOrderItem(
        documento_compras="4500098765",
        item="00010",
        valor_liquido=Decimal("12500.00"),
        quantidade=Decimal("100.000"),
    )
    assert item.raw_extra == {}


def test_po_item_rejects_negative_quantidade():
    with pytest.raises(ValidationError):
        PurchaseOrderItem(
            documento_compras="4500098765",
            item="00010",
            quantidade=Decimal("-1"),
        )


# ---------- ServicePackage ----------


def test_service_package_happy():
    sp = ServicePackage(
        pacote="0000000001",
        linha=1,
        numero_servico="SVC-001",
        preco_bruto=Decimal("125.50"),
    )
    assert sp.linha == 1


def test_service_package_rejects_negative_linha():
    with pytest.raises(ValidationError):
        ServicePackage(
            pacote="0000000001",
            linha=-1,
            numero_servico="SVC-001",
        )


# ---------- PurchaseOrderGc ----------


def test_po_gc_happy():
    gc = PurchaseOrderGc(
        documento_compras="4600012345",
        item="00010",
        empresa="0001",
        preco_bruto_lpu=Decimal("125.50"),
    )
    assert gc.raw_extra == {}


def test_po_gc_rejects_negative_preco_bruto_lpu():
    with pytest.raises(ValidationError):
        PurchaseOrderGc(
            documento_compras="x",
            item="00010",
            preco_bruto_lpu=Decimal("-0.01"),
        )


# ---------- CostCenterAccount ----------


def test_cca_happy():
    cca = CostCenterAccount(
        centro_de_custo="CC-1234",
        conta_razao="6010101",
    )
    assert cca.id is None  # SERIAL — antes do INSERT


def test_cca_requires_both_fields():
    with pytest.raises(ValidationError):
        CostCenterAccount(centro_de_custo="CC-1234")

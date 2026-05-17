"""Tests de SupplierBridge, ContractMaster, ContractVersion, ContractClause."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.domain.payments.contracts import (
    ContractClause,
    ContractMaster,
    ContractVersion,
    SupplierBridge,
)


# ---------- SupplierBridge ----------


def test_supplier_bridge_happy():
    sb = SupplierBridge(
        categoria="OBRAS CIVIS",
        empreiteira="ABILITY",
        contrato_num_sap="4600012345",
        ref_ws="WS-001",
        numero_fornecedor_sap="100200",
        cnpj="12345678000199",
    )
    assert sb.empreiteira == "ABILITY"
    assert sb.id is not None
    assert sb.created_at is not None


# ---------- ContractMaster ----------


def test_contract_master_happy():
    cm = ContractMaster(
        supplier_bridge_id=uuid4(),
        contrato_num_sap="4600012345",
        ref_ws="WS-001",
        cnpj="12345678000199",
        created_by_id=uuid4(),
    )
    assert cm.is_monitored is True
    assert cm.current_version_id is None


def test_contract_master_requires_created_by_id():
    """created_by_id é NOT NULL no DB — domain reflete isso."""
    with pytest.raises(ValidationError, match="created_by_id"):
        ContractMaster(
            supplier_bridge_id=uuid4(),
            contrato_num_sap="4600012345",
            ref_ws="WS-001",
            cnpj="12345678000199",
        )


# ---------- ContractVersion ----------


def _cv_kwargs(**overrides):
    base = dict(
        contract_master_id=uuid4(),
        version_number=1,
        valid_from=date(2024, 1, 1),
        valid_to=date(2025, 12, 31),
    )
    base.update(overrides)
    return base


def test_contract_version_happy():
    cv = ContractVersion(**_cv_kwargs(
        val_fix_cab=Decimal("1500000.00"),
        objeto_contrato="Manutenção de fibra óptica",
        uf=["RJ", "ES"],
        cidade=["Rio de Janeiro", "Vitória"],
    ))
    assert cv.valid_from < cv.valid_to
    assert cv.extracted_cost_brl == Decimal("0")


def test_contract_version_rejects_inverted_dates():
    with pytest.raises(ValidationError, match="valid_from"):
        ContractVersion(**_cv_kwargs(
            valid_from=date(2025, 12, 31),
            valid_to=date(2024, 1, 1),
        ))


def test_contract_version_accepts_same_day_validity():
    """Edge case: valid_from == valid_to é válido (contrato 1 dia)."""
    same = date(2025, 6, 1)
    cv = ContractVersion(**_cv_kwargs(valid_from=same, valid_to=same))
    assert cv.valid_from == cv.valid_to


def test_contract_version_rejects_uf_lowercase():
    with pytest.raises(ValidationError, match="UF"):
        ContractVersion(**_cv_kwargs(uf=["rj"]))


def test_contract_version_rejects_uf_wrong_length():
    with pytest.raises(ValidationError, match="UF"):
        ContractVersion(**_cv_kwargs(uf=["RJX"]))


def test_contract_version_rejects_negative_extracted_cost():
    with pytest.raises(ValidationError):
        ContractVersion(**_cv_kwargs(extracted_cost_brl=Decimal("-0.01")))


def test_contract_version_rejects_version_zero():
    with pytest.raises(ValidationError):
        ContractVersion(**_cv_kwargs(version_number=0))


# ---------- ContractClause ----------


def test_contract_clause_happy_no_embedding():
    cc = ContractClause(
        contract_version_id=uuid4(),
        texto="Cláusula 1.1 - O contratante deverá ...",
    )
    assert cc.embedding is None


def test_contract_clause_happy_with_embedding():
    cc = ContractClause(
        contract_version_id=uuid4(),
        texto="Cláusula 1.1",
        embedding=[0.01] * 1536,
    )
    assert cc.embedding is not None
    assert len(cc.embedding) == 1536


def test_contract_clause_rejects_wrong_embedding_dim():
    with pytest.raises(ValidationError):
        ContractClause(
            contract_version_id=uuid4(),
            texto="x",
            embedding=[0.0] * 1024,
        )


def test_contract_clause_rejects_zero_pagina():
    with pytest.raises(ValidationError):
        ContractClause(
            contract_version_id=uuid4(),
            texto="x",
            pagina_pdf=0,
        )

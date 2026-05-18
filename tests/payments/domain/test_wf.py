"""Tests do WFPayment — entidade mais complexa (30 cols + taxonomias)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.domain.payments.enums import Sistema, TipoDespesa
from app.core.domain.payments.wf import WFPayment


def _kwargs(**overrides):
    base = dict(
        os_num="OS-12345",
        data_pedido=date(2025, 6, 1),
    )
    base.update(overrides)
    return base


def test_happy_minimal():
    wf = WFPayment(**_kwargs())
    assert wf.id is None  # BIGSERIAL antes do INSERT
    assert wf.raw_extra == {}
    assert wf.sistema is None
    assert wf.tipo_de_despesa is None


def test_happy_full():
    wf = WFPayment(**_kwargs(
        sistema="WF1",
        pedido_num="4500098765",
        contrato_num="4600012345",
        item_num="00010",
        material_servico_num="SVC-001",
        valor_total_final=Decimal("12500.00"),
        valor_unitario=Decimal("125.50"),
        uf="RJ",
        tipo_de_despesa="OPEX",
        empreiteira="ABILITY",
        status_os="EXECUTADO",
        nivel_gerencial="Em Pagamento",
        malogro="OK",
        mes_medicao="2025/06",
        regional_soe_nova="RJ/ES",
        centro_de_custo="CC-1234",
    ))
    assert wf.sistema is Sistema.WF1
    assert wf.tipo_de_despesa is TipoDespesa.OPEX


def test_data_pedido_required():
    """data_pedido é partition key — não pode ser None."""
    with pytest.raises(ValidationError, match="data_pedido"):
        WFPayment(os_num="OS-12345")


def test_os_num_required():
    with pytest.raises(ValidationError, match="os_num"):
        WFPayment(data_pedido=date(2025, 6, 1))


def test_uf_must_be_uppercase_two_letters():
    with pytest.raises(ValidationError):
        WFPayment(**_kwargs(uf="rj"))
    with pytest.raises(ValidationError):
        WFPayment(**_kwargs(uf="RJX"))
    with pytest.raises(ValidationError):
        WFPayment(**_kwargs(uf="R1"))


def test_uf_accepts_all_states():
    """Spot-check em algumas UFs reais."""
    for uf in ["RJ", "SP", "MG", "ES", "DF", "AM"]:
        wf = WFPayment(**_kwargs(uf=uf))
        assert wf.uf == uf


def test_sistema_rejects_invalid():
    with pytest.raises(ValidationError):
        WFPayment(**_kwargs(sistema="WF3"))


def test_tipo_despesa_rejects_invalid():
    with pytest.raises(ValidationError):
        WFPayment(**_kwargs(tipo_de_despesa="OUTRO"))


def test_mes_medicao_accepts_valid_format():
    wf = WFPayment(**_kwargs(mes_medicao="2024/01"))
    assert wf.mes_medicao == "2024/01"
    wf = WFPayment(**_kwargs(mes_medicao="2024/12"))
    assert wf.mes_medicao == "2024/12"


def test_mes_medicao_accepts_string_literals():
    """Pré-B esperava só YYYY/MM, mas dados reais têm 'PREVISTO' etc.
    Domain aceita string livre — validação de formato no rules engine."""
    for value in ["PREVISTO", "ABERTO", "2024-05"]:
        wf = WFPayment(**_kwargs(mes_medicao=value))
        assert wf.mes_medicao == value


def test_rejects_negative_valores():
    with pytest.raises(ValidationError):
        WFPayment(**_kwargs(valor_total_final=Decimal("-1")))
    with pytest.raises(ValidationError):
        WFPayment(**_kwargs(valor_unitario=Decimal("-0.01")))


def test_raw_extra_accepts_arbitrary_dict():
    """raw_extra absorve ~50 cols vestigiais do Analítico WF."""
    extras = {"col_x": "abc", "col_y": 123, "col_z": [1, 2, 3]}
    wf = WFPayment(**_kwargs(raw_extra=extras))
    assert wf.raw_extra == extras

"""Tests do runner: coerce_value + project."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.adapters.sap.projections import (
    FieldMapping,
    coerce_value,
    project,
    resolve_entity,
)
from app.core.domain.payments import (
    CostCenterAccount,
    Sistema,
    SupplierBridge,
    TipoDespesa,
    WFPayment,
)

from tests.payments.projections.conftest import make_config


# ---------- coerce_value ----------


def test_coerce_str_default():
    assert coerce_value("hello", FieldMapping(source="X")) == "hello"


def test_coerce_str_strips_by_default():
    assert coerce_value("  hello  ", FieldMapping(source="X")) == "hello"


def test_coerce_empty_string_becomes_none():
    assert coerce_value("", FieldMapping(source="X")) is None
    assert coerce_value("   ", FieldMapping(source="X")) is None


def test_coerce_uses_default_when_none():
    fm = FieldMapping(source="X", default="fallback")
    assert coerce_value(None, fm) == "fallback"


def test_coerce_int_from_string():
    assert coerce_value("42", FieldMapping(source="X", type="int")) == 42


def test_coerce_int_from_native():
    assert coerce_value(42, FieldMapping(source="X", type="int")) == 42


def test_coerce_decimal_pt_br():
    assert (
        coerce_value("1.234,56", FieldMapping(source="X", type="decimal"))
        == Decimal("1234.56")
    )


def test_coerce_date_dotted():
    assert (
        coerce_value("06.09.2022", FieldMapping(source="X", type="date"))
        == date(2022, 9, 6)
    )


def test_coerce_date_datetime_native():
    assert (
        coerce_value(
            datetime(2022, 9, 6), FieldMapping(source="X", type="date")
        )
        == date(2022, 9, 6)
    )


def test_coerce_datetime_from_date():
    """type='datetime' converte date → datetime à meia-noite."""
    result = coerce_value(
        date(2022, 9, 6), FieldMapping(source="X", type="datetime")
    )
    assert result == datetime(2022, 9, 6, 0, 0)


def test_coerce_bool_truthy():
    fm = FieldMapping(source="X", type="bool")
    assert coerce_value("true", fm) is True
    assert coerce_value("SIM", fm) is True
    assert coerce_value("1", fm) is True


def test_coerce_bool_falsy():
    fm = FieldMapping(source="X", type="bool")
    assert coerce_value("false", fm) is False
    assert coerce_value("não", fm) is False
    assert coerce_value("0", fm) is False


def test_coerce_bool_invalid_raises():
    with pytest.raises(ValueError):
        coerce_value("maybe", FieldMapping(source="X", type="bool"))


def test_coerce_enum_resolves_by_name():
    fm = FieldMapping(source="X", type="enum", enum="Sistema")
    assert coerce_value("WF1", fm) is Sistema.WF1


def test_coerce_enum_rejects_invalid():
    fm = FieldMapping(source="X", type="enum", enum="Sistema")
    with pytest.raises(ValueError):
        coerce_value("WF99", fm)


def test_coerce_list_str_from_list():
    result = coerce_value(
        ["RJ", "ES"], FieldMapping(source="X", type="list_str")
    )
    assert result == ["RJ", "ES"]


def test_coerce_list_str_from_string():
    result = coerce_value(
        "RJ", FieldMapping(source="X", type="list_str")
    )
    assert result == ["RJ"]


# ---------- resolve_entity ----------


def test_resolve_entity_known():
    assert resolve_entity("WFPayment") is WFPayment
    assert resolve_entity("SupplierBridge") is SupplierBridge


def test_resolve_entity_unknown_raises():
    with pytest.raises(ValueError, match="not found"):
        resolve_entity("NonExistent")


def test_resolve_entity_rejects_non_model():
    """Sistema é Enum, não BaseModel — não deve resolver."""
    with pytest.raises(ValueError, match="not found"):
        resolve_entity("Sistema")


# ---------- project (end-to-end) ----------


def test_project_supplier_bridge():
    cfg = make_config(
        target_entity="SupplierBridge",
        columns={
            "categoria": {"source": "CAT", "required": True},
            "empreiteira": {"source": "EMP", "required": True},
            "contrato_num_sap": {"source": "CONT", "required": True},
            "ref_ws": {"source": "REF", "required": True},
            "numero_fornecedor_sap": {"source": "FORN", "required": True},
            "cnpj": {"source": "CNPJ", "required": True},
        },
    )
    rows = [
        {"CAT": "OBRAS", "EMP": "ABILITY", "CONT": "4600012345",
         "REF": "WS-001", "FORN": "100200", "CNPJ": "12345678000199"},
    ]
    result = list(project(cfg, iter(rows)))
    assert len(result) == 1
    assert isinstance(result[0], SupplierBridge)
    assert result[0].empreiteira == "ABILITY"
    assert result[0].cnpj == "12345678000199"


def test_project_required_field_missing_raises():
    cfg = make_config(
        target_entity="CostCenterAccount",
        columns={
            "centro_de_custo": {"source": "CC", "required": True},
            "conta_razao": {"source": "CONTA", "required": True},
        },
    )
    bad_row = {"CC": "CC-1"}  # falta CONTA
    with pytest.raises(ValueError, match="conta_razao"):
        list(project(cfg, iter([bad_row])))


def test_project_with_catchall_absorbs_unmapped():
    cfg = make_config(
        target_entity="WFPayment",
        columns={
            "os_num": {"source": "OS", "required": True},
            "data_pedido": {"source": "DT", "type": "date", "required": True},
        },
        catchall={"field": "raw_extra"},
    )
    row = {
        "OS": "OS-1",
        "DT": "01.06.2025",
        "UNKNOWN_COL": "extra value",
        "ANOTHER": 42,
    }
    result = list(project(cfg, iter([row])))
    assert len(result) == 1
    wf = result[0]
    assert wf.os_num == "OS-1"
    assert wf.data_pedido == date(2025, 6, 1)
    assert wf.raw_extra == {"UNKNOWN_COL": "extra value", "ANOTHER": 42}


def test_project_catchall_exclude_none_filters():
    """exclude_none=True não inclui keys com valor None."""
    cfg = make_config(
        target_entity="WFPayment",
        columns={
            "os_num": {"source": "OS", "required": True},
            "data_pedido": {"source": "DT", "type": "date", "required": True},
        },
        catchall={"field": "raw_extra", "exclude_none": True},
    )
    row = {"OS": "OS-1", "DT": "01.06.2025", "X": "v", "Y": None}
    result = list(project(cfg, iter([row])))
    assert result[0].raw_extra == {"X": "v"}  # Y omitido


def test_project_catchall_json_safe_converts_date():
    """raw_extra é JSONB no PG; date/datetime → ISO string."""
    cfg = make_config(
        target_entity="WFPayment",
        columns={
            "os_num": {"source": "OS", "required": True},
            "data_pedido": {"source": "DT", "type": "date", "required": True},
        },
        catchall={"field": "raw_extra"},
    )
    row = {
        "OS": "OS-1",
        "DT": "01.06.2025",
        "SOME_DATE": date(2024, 12, 31),
    }
    result = list(project(cfg, iter([row])))
    assert result[0].raw_extra["SOME_DATE"] == "2024-12-31"


def test_project_enum_coercion():
    cfg = make_config(
        target_entity="WFPayment",
        columns={
            "os_num": {"source": "OS", "required": True},
            "data_pedido": {"source": "DT", "type": "date", "required": True},
            "sistema": {"source": "SIS", "type": "enum", "enum": "Sistema"},
            "tipo_de_despesa": {
                "source": "TDD", "type": "enum", "enum": "TipoDespesa"
            },
        },
    )
    row = {
        "OS": "OS-1", "DT": "01.06.2025",
        "SIS": "WF2", "TDD": "CAPEX",
    }
    result = list(project(cfg, iter([row])))
    assert result[0].sistema is Sistema.WF2
    assert result[0].tipo_de_despesa is TipoDespesa.CAPEX


def test_project_defaults_applied():
    """defaults aplicam-se a TODOS os rows como kwargs base."""
    cfg = make_config(
        target_entity="CostCenterAccount",
        columns={
            "centro_de_custo": {"source": "CC", "required": True},
            "conta_razao": {"source": "CONTA", "required": True},
        },
        defaults={"id": None},  # explicitly None (default já é None mas testa propagação)
    )
    result = list(project(cfg, iter([{"CC": "CC-1", "CONTA": "6010"}])))
    assert isinstance(result[0], CostCenterAccount)
    assert result[0].centro_de_custo == "CC-1"


def test_project_is_lazy_generator():
    """project() é generator — não consome todo o input upfront."""
    cfg = make_config(
        target_entity="CostCenterAccount",
        columns={
            "centro_de_custo": {"source": "CC", "required": True},
            "conta_razao": {"source": "CONTA", "required": True},
        },
    )

    consumed = []

    def src():
        for i in range(3):
            consumed.append(i)
            yield {"CC": f"CC-{i}", "CONTA": "6010"}

    gen = project(cfg, src())
    first = next(gen)
    assert consumed == [0]  # só 1 row consumido
    assert first.centro_de_custo == "CC-0"
    list(gen)  # drain
    assert consumed == [0, 1, 2]

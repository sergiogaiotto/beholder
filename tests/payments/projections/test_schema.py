"""Tests do schema Pydantic dos YAMLs de projeção."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.adapters.sap.projections import (
    CatchallConfig,
    FieldMapping,
    ProjectionConfig,
    SourceConfig,
)


def test_field_mapping_minimal():
    fm = FieldMapping(source="HEADER")
    assert fm.source == "HEADER"
    assert fm.type == "str"
    assert fm.required is False
    assert fm.default is None


def test_field_mapping_rejects_unknown_type():
    with pytest.raises(ValidationError):
        FieldMapping(source="X", type="json")  # type não suportado


def test_field_mapping_rejects_extra_fields():
    with pytest.raises(ValidationError):
        FieldMapping(source="X", typo_field=True)


def test_source_config_xlsx_with_sheet():
    sc = SourceConfig(format="xlsx", sheet="Sheet1")
    assert sc.format == "xlsx"
    assert sc.sheet == "Sheet1"


def test_source_config_msrv5_no_sheet():
    sc = SourceConfig(format="msrv5", encoding="cp1252")
    assert sc.encoding == "cp1252"


def test_source_config_rejects_invalid_format():
    with pytest.raises(ValidationError):
        SourceConfig(format="csv")


def test_catchall_config():
    cc = CatchallConfig(field="raw_extra")
    assert cc.include_unmapped is True
    assert cc.exclude_none is True


def test_projection_config_full():
    cfg = ProjectionConfig.model_validate({
        "target_entity": "WFPayment",
        "description": "Test",
        "source": {"format": "xlsx", "sheet": "Sheet1"},
        "columns": {
            "os_num": {"source": "OS", "required": True},
            "data_pedido": {"source": "DATA_PEDIDO", "type": "date", "required": True},
        },
        "catchall": {"field": "raw_extra"},
        "defaults": {"sistema": "WF1"},
    })
    assert cfg.target_entity == "WFPayment"
    assert len(cfg.columns) == 2
    assert cfg.catchall.field == "raw_extra"
    assert cfg.defaults["sistema"] == "WF1"


def test_projection_config_requires_at_least_one_column():
    with pytest.raises(ValidationError):
        ProjectionConfig.model_validate({
            "target_entity": "WFPayment",
            "source": {"format": "xlsx"},
            "columns": {},
        })


def test_projection_config_rejects_extra_top_level():
    with pytest.raises(ValidationError):
        ProjectionConfig.model_validate({
            "target_entity": "WFPayment",
            "source": {"format": "xlsx"},
            "columns": {"os_num": {"source": "OS"}},
            "unknown_key": "x",
        })

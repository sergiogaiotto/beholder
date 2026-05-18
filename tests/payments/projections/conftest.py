"""Fixtures para tests das projeções."""

from __future__ import annotations

from app.adapters.sap.projections import ProjectionConfig, SourceConfig


def make_config(
    *,
    target_entity: str,
    columns: dict,
    catchall=None,
    defaults: dict | None = None,
    source_format: str = "xlsx",
    sheet: str | None = "Sheet1",
) -> ProjectionConfig:
    """Helper para construir configs inline em tests sem repetir boilerplate."""
    return ProjectionConfig.model_validate({
        "target_entity": target_entity,
        "source": {"format": source_format, "sheet": sheet},
        "columns": columns,
        "catchall": catchall,
        "defaults": defaults or {},
    })

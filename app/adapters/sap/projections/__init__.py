"""Projeções YAML — mapeia dicts crus (parsers) para domain models payments.

  schema.py    — Pydantic models que validam o YAML config
  runner.py    — load_projection + project (Iterator[dict] → Iterator[BaseModel])
  configs/     — 7 YAMLs (1 por XLSX fonte da Fase 1)
"""

from app.adapters.sap.projections.runner import (
    PROJECTIONS_DIR,
    coerce_value,
    list_projections,
    load_projection,
    project,
    resolve_entity,
)
from app.adapters.sap.projections.schema import (
    CatchallConfig,
    FieldMapping,
    FieldType,
    ProjectionConfig,
    SourceConfig,
)

__all__ = [
    "ProjectionConfig",
    "SourceConfig",
    "FieldMapping",
    "FieldType",
    "CatchallConfig",
    "PROJECTIONS_DIR",
    "load_projection",
    "list_projections",
    "project",
    "resolve_entity",
    "coerce_value",
]

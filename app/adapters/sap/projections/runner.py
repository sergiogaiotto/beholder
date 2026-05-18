"""Runner das projeções — carrega YAML, valida, aplica em Iterator[dict].

API principal:
  load_projection(yaml_path) -> ProjectionConfig
  list_projections() -> dict[str, Path]    # disponíveis em configs/
  project(config, src_iter) -> Iterator[BaseModel]
  coerce_value(raw, mapping) -> Any        # útil pra debugging
  resolve_entity(name) -> type[BaseModel]
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.adapters.sap.parsers._helpers import parse_date_br, parse_decimal_br
from app.adapters.sap.projections.schema import (
    FieldMapping,
    ProjectionConfig,
)
from app.core.domain import payments as _payments_domain


PROJECTIONS_DIR = Path(__file__).parent / "configs"


@dataclass
class ProjectStats:
    """Contadores opcionais — passados ao project() para o loader (F) tracking."""

    rows_seen: int = 0
    """Total de rows iteradas do source (inclui yielded + skipped)."""

    rows_yielded: int = 0
    """Rows que viraram domain models (foram pra batch do loader)."""

    rows_skipped: int = 0
    """Rows descartadas por on_missing='skip_row' em campos required."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_projection(yaml_path: Path | str) -> ProjectionConfig:
    """Carrega + valida 1 YAML. ValidationError se schema fora do esperado."""
    p = Path(yaml_path)
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{p.name}: YAML root deve ser dict, got {type(data).__name__}")
    return ProjectionConfig.model_validate(data)


def list_projections(directory: Path | str | None = None) -> dict[str, Path]:
    """Retorna {nome_sem_ext: caminho} dos YAMLs em `directory`.

    Default: PROJECTIONS_DIR (app/adapters/sap/projections/configs/).
    """
    d = Path(directory) if directory else PROJECTIONS_DIR
    if not d.is_dir():
        return {}
    return {
        p.stem: p
        for p in sorted(d.iterdir())
        if p.is_file() and p.suffix in (".yaml", ".yml")
    }


def project(
    config: ProjectionConfig,
    src_iter: Iterator[dict[str, Any]],
    *,
    stats: ProjectStats | None = None,
) -> Iterator[BaseModel]:
    """Itera src_iter (output de parsers) e yields domain models projetados.

    Comportamento em campos required ausentes:
      - on_missing='raise' (default): ValueError aborta toda a ingestão.
      - on_missing='skip_row': row inteira descartada; se `stats` fornecido,
        incrementa rows_skipped.

    Catchall absorve unmapped keys conforme `config.catchall`.

    Args:
      stats: opcional ProjectStats — recebe contadores de rows_seen/yielded/skipped.
    """
    target_cls = resolve_entity(config.target_entity)
    mapped_sources = {m.source for m in config.columns.values()}

    for src_row in src_iter:
        if stats is not None:
            stats.rows_seen += 1

        kwargs: dict[str, Any] = dict(config.defaults)
        skip_row = False

        for target_field, mapping in config.columns.items():
            raw = src_row.get(mapping.source)
            value = coerce_value(raw, mapping)
            if value is None and mapping.required:
                if mapping.on_missing == "skip_row":
                    skip_row = True
                    break
                raise ValueError(
                    f"{config.target_entity}.{target_field}: required field "
                    f"missing from source key {mapping.source!r}"
                )
            kwargs[target_field] = value

        if skip_row:
            if stats is not None:
                stats.rows_skipped += 1
            continue

        if config.catchall is not None and config.catchall.include_unmapped:
            extras = {
                k: v
                for k, v in src_row.items()
                if k not in mapped_sources
                and (not config.catchall.exclude_none or v is not None)
            }
            kwargs[config.catchall.field] = _json_safe(extras)

        if stats is not None:
            stats.rows_yielded += 1
        yield target_cls(**kwargs)


def resolve_entity(name: str) -> type[BaseModel]:
    """Resolve string → class do model em app.core.domain.payments."""
    cls = getattr(_payments_domain, name, None)
    if cls is None or not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        raise ValueError(
            f"target_entity {name!r} not found in app.core.domain.payments"
        )
    return cls


def _resolve_enum(name: str) -> type[Enum]:
    cls = getattr(_payments_domain, name, None)
    if cls is None or not (isinstance(cls, type) and issubclass(cls, Enum)):
        raise ValueError(
            f"enum {name!r} not found in app.core.domain.payments enums"
        )
    return cls


def coerce_value(raw: Any, mapping: FieldMapping) -> Any:
    """Converte valor cru do source para o tipo target conforme mapping.

    Retorna None se raw é None ou string vazia (e coerce_empty_to_none=True).
    Para `required=True`, o caller (`project`) é quem levanta — aqui só convertemos.
    """
    if raw is None:
        return mapping.default

    if isinstance(raw, str):
        if mapping.strip:
            raw = raw.strip()
        if raw == "" and mapping.coerce_empty_to_none:
            return mapping.default

    t = mapping.type

    if t == "str":
        return str(raw)

    if t == "int":
        if isinstance(raw, int) and not isinstance(raw, bool):
            return raw
        return int(str(raw).replace(".", "").replace(",", ""))

    if t == "decimal":
        return parse_decimal_br(raw)

    if t == "date":
        return parse_date_br(raw)

    if t == "datetime":
        # openpyxl já retorna datetime; outros caminhos passam por parse_date_br.
        from datetime import date, datetime
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, date):
            return datetime(raw.year, raw.month, raw.day)
        d = parse_date_br(raw)
        return datetime(d.year, d.month, d.day) if d else None

    if t == "bool":
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("true", "sim", "1", "s", "yes", "y"):
            return True
        if s in ("false", "nao", "não", "0", "n", "no"):
            return False
        raise ValueError(f"bool não-interpretável: {raw!r}")

    if t == "enum":
        if mapping.enum is None:
            raise ValueError("type='enum' exige campo 'enum' (nome do Enum)")
        enum_cls = _resolve_enum(mapping.enum)
        return enum_cls(raw)

    if t == "list_str":
        if isinstance(raw, list):
            return [str(x) for x in raw]
        # String simples → lista de 1
        return [str(raw)]

    raise ValueError(f"FieldMapping.type não suportado: {t!r}")


def _json_safe(d: dict[str, Any]) -> dict[str, Any]:
    """Converte valores não-JSON-serializáveis (date, datetime, Decimal) para str.

    raw_extra é JSONB no PG; asyncpg/json não serializam date/datetime/Decimal
    direto. Aqui convertemos para string ISO para preservar info.
    """
    from datetime import date, datetime
    from decimal import Decimal

    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, date):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = str(v)
        else:
            out[k] = v
    return out

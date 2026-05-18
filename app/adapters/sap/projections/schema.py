"""Schema dos YAMLs de projeção — Pydantic v2.

Cada YAML em configs/ é validado por ProjectionConfig.model_validate()
antes de ser usado pelo runner. Erro de YAML mal-formado falha cedo,
antes de qualquer linha de dados ser processada.

Convenções:
  - target_entity: nome do model em `app.core.domain.payments` (ex: 'WFPayment')
  - source.format: 'xlsx' | 'msrv5' (msrv5 é o TXT pipe-delimited)
  - columns: dict {target_field: FieldMapping}; ordem importa só
    cosmeticamente (Python 3.7+ preserva)
  - catchall: campo destino (ex: 'raw_extra') que recebe TUDO que não foi
    mapeado em `columns`
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FieldType = Literal[
    "str",
    "int",
    "decimal",
    "date",
    "datetime",
    "bool",
    "enum",
    "list_str",  # TEXT[] no PG; aceita lista ou string que vira [string]
]


class SourceConfig(BaseModel):
    """Configuração do arquivo fonte."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["xlsx", "msrv5"]
    sheet: str | None = None  # obrigatório para xlsx com múltiplas sheets
    encoding: str | None = None  # default cp1252 para msrv5; ignorado em xlsx


class FieldMapping(BaseModel):
    """Mapeamento de 1 campo do source para 1 atributo do domain model."""

    model_config = ConfigDict(extra="forbid")

    source: str
    """Nome do header no source file (preservar maiúscula/acento)."""

    type: FieldType = "str"
    """Tipo Python alvo. Determinismo importa: nunca infira do valor."""

    enum: str | None = None
    """Nome do Enum em `app.core.domain.payments` (obrigatório se type='enum')."""

    required: bool = False
    """Se True, fonte ausente/None levanta ValueError."""

    default: Any = None
    """Valor a usar quando fonte é None/vazio. Ignorado se required=True."""

    coerce_empty_to_none: bool = True
    """String vazia (após strip) vira None ao invés de '' / Decimal(0)."""

    strip: bool = True
    """Aplica .strip() em valores string antes de coerção."""


class LoadConfig(BaseModel):
    """Como o loader (Bloco F) deve persistir os models projetados."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["bulk_insert", "bulk_upsert"] = "bulk_insert"
    """Método do repo a invocar. bulk_upsert pra catálogos (SupplierBridge,
    CostCenterAccount); bulk_insert (com ON CONFLICT DO NOTHING) pro resto."""

    batch_size: int = Field(default=10_000, ge=1)
    """Tamanho do batch passado pro repo. 10k é razoável pra asyncpg
    executemany; tunar via Bloco G acceptance gate."""


class CatchallConfig(BaseModel):
    """Configuração do campo catch-all (ex: raw_extra)."""

    model_config = ConfigDict(extra="forbid")

    field: str
    """Nome do atributo no domain (ex: 'raw_extra')."""

    include_unmapped: bool = True
    """Se True, headers não mapeados em `columns` vão pra cá (com valor original)."""

    exclude_none: bool = True
    """Se True, valores None não entram no dict (mantém compacto)."""


class ProjectionConfig(BaseModel):
    """Configuração completa de 1 projeção (1 YAML = 1 source → 1 entity)."""

    model_config = ConfigDict(extra="forbid")

    target_entity: str
    """Nome do domain model em `app.core.domain.payments`."""

    description: str | None = None
    """Documentação humana — não usada pelo runner."""

    source: SourceConfig
    columns: dict[str, FieldMapping] = Field(min_length=1)
    catchall: CatchallConfig | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    """Valores fixos aplicados a todos os rows (ex: source='msrv5')."""

    load: LoadConfig = Field(default_factory=LoadConfig)
    """Método de persistência (loader F). Default: bulk_insert / batch 10k."""

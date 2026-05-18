"""Base model + type aliases compartilhados do domínio payments.

PaymentsBaseModel: ConfigDict padrão p/ todos os modelos do domínio.

  - from_attributes=True
        Aceita asyncpg.Record / objeto / dict via .model_validate().
        Repos vão fazer Model.model_validate(row) direto do asyncpg.

  - extra="forbid"
        Impede typos silenciosos ao construir via dict. Se o DB ganhar
        nova coluna não mapeada aqui, vai falhar explicitamente no model_validate
        — gatilho para atualizar o domain antes que dados se percam.

  - str_strip_whitespace=True
        XLSX SAP têm campos com whitespace marginal — trata na entrada.

  - populate_by_name=True
        Habilita alias para serialização futura (API responses com snake_case
        no DB e camelCase no JSON, sem código adicional).

  - validate_assignment=False (default)
        Performance: ingestão de 1M+ rows não pode pagar overhead de re-validação
        a cada setattr. Quem precisa de mutação validada usa model_copy(update=...).

Type aliases monetários: SEMPRE Decimal (nunca float). Floating-point drift
em valores monetários é causa #1 de findings espúrios em reconciliação.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class PaymentsBaseModel(BaseModel):
    """Base para todos os modelos do domínio payments."""

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


# ----------------------------------------------------------------------------
# Type aliases — usados em ContractVersion, LPUItem, WFPayment, SAP entities
# ----------------------------------------------------------------------------

Money = Annotated[Decimal, Field(ge=Decimal("0"))]
"""Valor monetário não-negativo (Decimal). Use para NUMERIC(*,2) / NUMERIC(*,4)."""

Quantity = Annotated[Decimal, Field(ge=Decimal("0"))]
"""Quantidade não-negativa (Decimal). NUMERIC(15,3) / NUMERIC(15,4)."""

Pct01 = Annotated[float, Field(ge=0.0, le=1.0)]
"""Fração 0-1 (confidence, threshold fuzzy, etc.)."""

NonNegInt = Annotated[int, Field(ge=0)]
"""Inteiro não-negativo (contadores)."""

PosInt = Annotated[int, Field(ge=1)]
"""Inteiro positivo (version_number, etc.)."""

EmbeddingVector = Annotated[list[float], Field(min_length=1536, max_length=1536)]
"""Embedding pgvector(1536) — compatível OpenAI text-embedding-3-small / Maritaca."""

"""Helpers internos dos repos payments (não exportar fora do pacote).

  record_to_dict: asyncpg.Record → dict pra Pydantic.model_validate()
  parse_pgvector: string '[0.1,0.2,...]' → list[float]
  format_pgvector: list[float] → string '[0.1,0.2,...]' para INSERT
"""

from __future__ import annotations

from typing import Any


def record_to_dict(record: Any) -> dict[str, Any]:
    """Converte asyncpg.Record (ou None) para dict.

    asyncpg.Record só suporta acesso via [] e .keys() — não tem __getattr__.
    Pydantic v2 com from_attributes=True precisa de atributos OU dict.
    """
    if record is None:
        return {}
    return dict(record)


def parse_pgvector(raw: str | None) -> list[float] | None:
    """Parse retorno de pgvector vector(N) — vem como string '[0.1,0.2,...]'.

    None se NULL no DB. Lista vazia [] se vector vazio (não deve acontecer
    para vector(1536) mas tratamos defensivamente).
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s.startswith("[") or not s.endswith("]"):
        raise ValueError(f"pgvector malformado: {raw!r}")
    inner = s[1:-1].strip()
    if not inner:
        return []
    return [float(x) for x in inner.split(",")]


def format_pgvector(values: list[float] | None) -> str | None:
    """list[float] → string '[v1,v2,...]' aceita pelo pgvector via SET."""
    if values is None:
        return None
    return "[" + ",".join(repr(float(v)) for v in values) + "]"

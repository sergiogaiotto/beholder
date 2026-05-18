"""Helpers de parsing comuns aos parsers SAP/WF.

  parse_date_br       — "dd.mm.yyyy" ou "dd/mm/yyyy" → date | None
  parse_decimal_br    — "1.234,56" (pt-BR) → Decimal | None (preserva sinal)
  normalize_header    — strip whitespace + case-preserving + remoção de
                        chars invisíveis (NBSP, BOM); idempotente.

Todas as funções aceitam None ou string vazia → retornam None (não levantam).
Inputs claramente inválidos (não-numérico em decimal, formato inesperado em
data) levantam ValueError para fail-fast.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# Caracteres invisíveis comuns em exports SAP/Excel.
_INVISIBLE_CHARS = " ﻿​‌‍⁠"


def normalize_header(raw: Any) -> str:
    """Limpa cabeçalho de coluna: strip + remove NBSP/BOM. Idempotente."""
    if raw is None:
        return ""
    s = str(raw)
    for ch in _INVISIBLE_CHARS:
        s = s.replace(ch, " ")
    return s.strip()


def parse_date_br(raw: Any) -> date | None:
    """Aceita dd.mm.yyyy, dd/mm/yyyy, datetime, date, ou None/empty.

    Reconhece os formatos comuns no MSRV5 (dd.mm.yyyy) e XLSX SAP
    (datetime nativo via openpyxl). Retorna None para vazio; levanta
    ValueError para formato desconhecido.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    # Tenta dd.mm.yyyy e dd/mm/yyyy
    for sep in (".", "/", "-"):
        if sep in s:
            parts = s.split(sep)
            if len(parts) == 3 and len(parts[0]) <= 2 and len(parts[2]) == 4:
                try:
                    d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                    return date(y, m, d)
                except (ValueError, TypeError):
                    pass
            # YYYY-MM-DD (ISO)
            if len(parts) == 3 and len(parts[0]) == 4:
                try:
                    return date(int(parts[0]), int(parts[1]), int(parts[2]))
                except (ValueError, TypeError):
                    pass
    raise ValueError(f"data em formato desconhecido: {raw!r}")


def parse_decimal_br(raw: Any) -> Decimal | None:
    """Aceita "1.234,56" (pt-BR), "1234.56" (ISO), Decimal, int, float, ou None.

    Preserva sinal negativo. Vírgula é sempre tratada como separador decimal.
    Ponto é sempre tratado como separador de milhar (compatível com pt-BR
    e com cp1252 SAP). Para input ISO ("1234.56" sem vírgula), o ponto é
    o decimal — heurística: se há vírgula, ponto é milhar; se não há,
    ponto é decimal.

    Retorna None para vazio; levanta ValueError para input não-numérico.
    """
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace(" ", "")
    has_comma = "," in s
    has_dot = "." in s
    if has_comma:
        # pt-BR: ponto é milhar, vírgula é decimal
        s = s.replace(".", "").replace(",", ".")
    elif has_dot:
        # ISO (ou heurística): ponto é decimal — manter
        pass
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"decimal em formato desconhecido: {raw!r}") from None

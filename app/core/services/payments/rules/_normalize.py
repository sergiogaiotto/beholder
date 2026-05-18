"""Text normalization para fuzzy matching das regras R5/R6.8.

  normalize_text("São Paulo - SP") == "sao paulo - sp"
  normalize_text(None) == ""

Estratégia:
  1. unidecode → remove acentos (UTF-8 → ASCII)
  2. lower
  3. collapse whitespace (qualquer sequência de espaços vira 1)
  4. strip

Não remove hífen/pontuação intencionalmente — preserva semântica.
RapidFuzz.partial_ratio é robusto a essas variações; normalização agressiva
demais (remover tudo não-alfa) gera falsos positivos.
"""

from __future__ import annotations

from unidecode import unidecode


def normalize_text(s: str | None) -> str:
    """Lowercase + sem acentos + whitespace colapsado. None → ''."""
    if not s:
        return ""
    decoded = unidecode(str(s))
    return " ".join(decoded.lower().split())

"""Parser do MSRV5 — TXT pipe-delimited cp1252 (validado em Pré-B).

Volume: 3.103.381 linhas totais, 2.909.414 data rows (Pré-B).
Distribuição: 4,7% separadores `---`, 1,6% headers repetidos (page breaks SAP),
0 linhas malformadas.

Schema fixo (7 colunas):
  | Data doc.  | N° de docu | Item | Serviço | Qtd.   | Preço bruto | Texto breve |
  | 06.09.2022 | 5700012782 | 7913 | 9000507 | 0,000  |  2,71       | SERV CONFECCAO MATERIAL GRAFICO |

Decisões:
  - Streaming line-by-line — never carrega o arquivo inteiro.
  - cp1252 com errors='replace' (Pré-B confirmou 0 problemas; replace é
    rede de segurança).
  - parse_msrv5 retorna linhas já tipadas (date, Decimal, int) — economia
    de uma transformação no projeto.
  - iter_msrv5_rows retorna dicts crus (string) — pra usar em test/debug
    e composição com projection layer.

A função `parse_msrv5` é a interface principal: retorna dicts com keys
canônicas (data_documento, documento_compras, item, numero_servico,
qtd_solicitada, preco_unitario, texto_breve) e valores tipados.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.adapters.sap.parsers._helpers import parse_date_br, parse_decimal_br

# Schema fixo do MSRV5 (Pré-B §2.2)
MSRV5_COLUMNS: tuple[str, ...] = (
    "data_documento",
    "documento_compras",
    "item",
    "numero_servico",
    "qtd_solicitada",
    "preco_unitario",
    "texto_breve",
)

# Linhas como "---" ou "------" entre páginas SAP
_SEPARATOR_RE = re.compile(r"^-+$")

# Header repetido das page breaks: "Data doc."
_HEADER_FIRST_CELL = "Data doc."

# Variações de header de footer SAP (page break)
_FOOTER_MARKERS = ("Estat", "Page", "Página")


def iter_msrv5_rows(
    path: Path | str,
    *,
    encoding: str = "cp1252",
    errors: str = "replace",
) -> Iterator[list[str]]:
    """Itera linhas válidas do MSRV5 como listas de strings (sem parse de tipos).

    Skipa: linhas vazias, separadores `---`, headers repetidos, footers SAP,
    linhas malformadas (≠ 7 cols entre `|`).

    Yields: list[str] com 7 elementos (já com strip).
    """
    p = Path(path)
    with p.open("r", encoding=encoding, errors=errors) as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            if _SEPARATOR_RE.match(line):
                continue
            if not (line.startswith("|") and line.endswith("|")):
                continue
            cols = [c.strip() for c in line[1:-1].split("|")]
            if len(cols) != 7:
                continue
            # Header repetido (page break SAP)
            if cols[0] == _HEADER_FIRST_CELL:
                continue
            # Footer/metadata SAP
            if any(m in cols[0] for m in _FOOTER_MARKERS):
                continue
            yield cols


def parse_msrv5(
    path: Path | str,
    *,
    encoding: str = "cp1252",
    errors: str = "replace",
) -> Iterator[dict[str, Any]]:
    """Interface principal — yields dicts com keys canônicas e valores tipados.

    Tipos: data_documento=date, documento_compras=str, item=int,
    numero_servico=str, qtd_solicitada=Decimal, preco_unitario=Decimal,
    texto_breve=str.

    Linhas com erro de parse (data malformada, decimal não-numérico) são
    skipadas silenciosamente — o `IngestionRun` registra contagem total
    no `rows_skipped`. Pré-B confirmou 0 malformadas em 3.1M linhas.
    """
    for cols in iter_msrv5_rows(path, encoding=encoding, errors=errors):
        try:
            data_doc = parse_date_br(cols[0])
            if data_doc is None:
                continue
            yield {
                "data_documento": data_doc,
                "documento_compras": cols[1],
                "item": int(cols[2]) if cols[2] else None,
                "numero_servico": cols[3],
                "qtd_solicitada": parse_decimal_br(cols[4]),
                "preco_unitario": parse_decimal_br(cols[5]),
                "texto_breve": cols[6],
            }
        except (ValueError, IndexError):
            # Linha tipo-incompatível — skipa. Quem precisa de contagem
            # exata usa iter_msrv5_rows e faz o parse com tratamento próprio.
            continue

"""Streaming parsers para fontes SAP/WF.

  msrv5.py        — TXT pipe-delimited cp1252 (3.1M linhas LPU)
  wf_analytics.py — XLSX 869k × 81 cols (sheet Analítico WF)
  xlsx_stream.py  — helper genérico de XLSX read-only streaming (openpyxl)
  _helpers.py     — parse_date_br, parse_decimal_br, normalize_header

Parsers retornam Iterator[dict[str, Any]] com as colunas crus do arquivo
(headers normalizados, mas valores não projetados para domain models).
A projeção dict → domain model fica no Bloco E (YAMLs).
"""

from app.adapters.sap.parsers.msrv5 import (
    MSRV5_COLUMNS,
    iter_msrv5_rows,
    parse_msrv5,
)
from app.adapters.sap.parsers.wf_analytics import (
    WF_ANALYTICS_DEFAULT_SHEET,
    WF_ANALYTICS_EXPECTED_HEADERS,
    iter_wf_analytics_rows,
    parse_wf_analytics,
)
from app.adapters.sap.parsers.xlsx_stream import iter_xlsx_rows

__all__ = [
    # MSRV5
    "MSRV5_COLUMNS",
    "iter_msrv5_rows",
    "parse_msrv5",
    # WF Analítico
    "WF_ANALYTICS_DEFAULT_SHEET",
    "WF_ANALYTICS_EXPECTED_HEADERS",
    "iter_wf_analytics_rows",
    "parse_wf_analytics",
    # Generic XLSX
    "iter_xlsx_rows",
]

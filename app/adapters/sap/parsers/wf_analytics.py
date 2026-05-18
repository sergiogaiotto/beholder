"""Parser do Analítico WF — XLSX 869k × 81 cols (validado em Pré-B).

Arquivo: `Analitico_Empreiteiras_WF1_WF2_TOTAL_2025 2.txt.xlsx`
Sheet primária: `Analitico_Empreiteiras_WF1_WF2_`

Os 81 headers oficiais (Pré-B §3) são usados como contrato de schema:
validamos que o arquivo recebido tem todos os esperados, com nomes case-
insensitive. Cols ausentes levantam ValueError (fail-fast); cols extras
são aceitas (forward-compat) e ignoradas pelo projection layer (Bloco E).

iter_wf_analytics_rows: yields dicts {HEADER: raw_value} — 1 dict por linha
de dados. Headers preservados como aparecem (maiúsculas + underscore).
Valores nativos do openpyxl (datetime, int, float, str, None).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.adapters.sap.parsers.xlsx_stream import iter_xlsx_rows

WF_ANALYTICS_DEFAULT_SHEET = "Analitico_Empreiteiras_WF1_WF2_"

# Os 81 headers oficiais — Pré-B §3 (probe_analitico_wf.json).
# Usados para validação de schema na primeira linha. Não impõe ordem
# (validação é set-based em xlsx_stream._validate_headers).
WF_ANALYTICS_EXPECTED_HEADERS: tuple[str, ...] = (
    "SISTEMA", "CATEGORIA", "OS", "SOLID", "CIDADE", "UF",
    "REGIONAL_SOE_NOVA", "PROJETO", "PROJETO_GERENCIAL", "TECNOLOGIA",
    "EMPREITEIRA", "ACAO", "ATIVIDADE", "FASE_ATUAL", "STATUS_OS",
    "MALOGRO", "CONTRATO_NUM", "ITEM_NUM", "ITEM_DESCRICAO",
    "MATERIAL_SERVICO_NUM", "TIPO_SER_MAT", "TIPO_DE_LPU", "TIPO_DE_DESPESA",
    "OBJETO_DO_CONTRATO", "PAGAMENTO", "QUANTIDADE", "VALOR_UNITARIO",
    "VALOR_TOTAL", "DEGRAU", "ID_CONTRATO_ITEM", "STATUS_DEGRAU",
    "ITEM_PARA", "VALOR_UNITARIO_PARA", "VALOR_TOTAL_FINAL", "STATUS_ITEM",
    "NIVEL", "NIVEL_DESCRICAO", "NIVEL_GERENCIAL", "DATA_CADASTRO",
    "DATA_FIM_EXECUCAO", "DATA_EXECUCAO", "DATA_CANCELAMENTO",
    "DATA_INCLUSAO", "USER_INCLUSAO", "DATA_APROVACAO_HORA",
    "USER_APROVACAO", "MES_MEDICAO", "PERIODO", "CENTRO_DE_CUSTO", "PEP",
    "NUMERO_MEDICAO", "LINHA_CONTROLE", "REQ_NUM", "REQ_ITEM", "DATA_REQ",
    "REQ_CRIADA_POR", "VALOR_REQ", "PEDIDO_NUM", "PEDIDO_ITEM",
    "DATA_PEDIDO", "PEDIDO_CRIADO_POR", "PEDIDO_VALOR",
    "ORDEM_INTERNA_CUSTO", "NOME_CENTRO_CUSTO", "ATUALIZACAO", "MES_APROV",
    "SITUACAO", "CONTA_RAZAO", "ORDEM_INTERNA", "NUMERO_FORNECEDOR",
    "SIGLA_DA_ESTACAO", "UNIDADE_DE_NEGOCIO", "AREA_DE_ATUACAO", "SERVICO",
    "ATIVIDADE_INFRA", "TIPO_DE_ATIVIDADE_INFRA", "SEGMENTO",
    "TIPO_DE_INCIDENTE", "ID_SAP", "FASE_ATUAL_DE_PARA", "CLIENTE",
)


def iter_wf_analytics_rows(
    path: Path | str,
    *,
    sheet_name: str = WF_ANALYTICS_DEFAULT_SHEET,
    validate_schema: bool = True,
) -> Iterator[dict[str, Any]]:
    """Yields linhas do Analítico WF como dicts {HEADER: valor_nativo}.

    Args:
      path: caminho do XLSX.
      sheet_name: nome da sheet. Default é a primary sheet do Pré-B.
      validate_schema: se True (default), exige que os 81 headers oficiais
        estejam presentes. Cols extras são aceitas.

    Yields: dict com keys = headers normalizados; valores nativos
    (datetime, int, float, str, None).
    """
    expected = WF_ANALYTICS_EXPECTED_HEADERS if validate_schema else None
    yield from iter_xlsx_rows(
        path,
        sheet_name=sheet_name,
        expected_headers=expected,
        skip_empty_rows=True,
    )


def parse_wf_analytics(
    path: Path | str,
    *,
    sheet_name: str = WF_ANALYTICS_DEFAULT_SHEET,
    validate_schema: bool = True,
) -> Iterator[dict[str, Any]]:
    """Alias semântico de `iter_wf_analytics_rows` — espelha API de parse_msrv5.

    Mantido como interface estável caso futuramente venha a fazer parse
    tipado (date, Decimal) aqui — hoje o XLSX já entrega tipos nativos
    via openpyxl, então é passthrough.
    """
    yield from iter_wf_analytics_rows(
        path, sheet_name=sheet_name, validate_schema=validate_schema
    )

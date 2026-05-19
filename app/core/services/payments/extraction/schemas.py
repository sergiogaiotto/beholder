"""Pydantic schemas usados pelo Instructor pra estruturar a extração R7
(Fase 4 — PDF Extraction).

Alinha com migration 002 (ContractVersion + LPUItem). Campos opcionais
(`| None = None`) refletem que nem todo contrato traz todos — confidence
fica abaixo do threshold quando faltam.

Pré-C validou que Maritaca sabia-4 tipicamente preenche 86% destes
campos via Instructor + few-shot. UX: campos vazios viram inputs em
branco no template HITL pra controladoria preencher manual.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class ExtractedLPUItem(BaseModel):
    """1 linha da Lista de Preços Unitários (LPU) extraída do PDF.

    Quantidade implícita não é extraída — `valor_total = preco_unitario × qtd`
    é calculado em runtime. Aqui só `preco_unitario`."""

    numero_servico: str = Field(description="código do serviço, ex: 'SRV.001'")
    descricao: str = Field(description="descrição completa do serviço")
    preco_unitario: Decimal = Field(description="preço unitário em R$ (decimal)")
    pagina_pdf: int | None = Field(
        default=None, description="página do PDF onde aparece a linha (1-indexed)"
    )


class ExtractedContractFields(BaseModel):
    """Folha de rosto + LPU items extraídos de 1 PDF de contrato.

    Estrutura alinhada com o schema da Pré-C (`docs/PRE_C_FINDINGS.md`).
    """

    # Identificação
    empreiteira_nome: str | None = Field(
        default=None,
        description="nome jurídico da empresa empreiteira (contratada)",
    )
    empreiteira_cnpj: str | None = Field(
        default=None,
        description="CNPJ da empreiteira (14 dígitos, só números)",
    )
    contratante_cnpj: str | None = Field(
        default=None,
        description="CNPJ da Claro/contratante (14 dígitos)",
    )

    # Escopo
    objeto_contrato: str | None = Field(
        default=None, description="descrição livre do objeto do contrato"
    )
    categoria: str | None = Field(
        default=None,
        description="taxonomia interna: FIXO MENSAL / RECUPERAÇÃO / MANUTENÇÃO / etc.",
    )
    tecnologia: str | None = Field(
        default=None, description="tecnologia: FIBRA / HFC / GPON / etc."
    )
    atividade: str | None = Field(
        default=None,
        description="atividade específica (MANUTENÇÃO PREVENTIVA, CABEAMENTO, etc.)",
    )
    uf: list[str] = Field(
        default_factory=list, description="UFs cobertas pelo contrato"
    )
    cidade: list[str] = Field(
        default_factory=list, description="cidades cobertas (vazio = todas da UF)"
    )

    # Financeiro
    val_fix_cab: Decimal | None = Field(
        default=None,
        description="valor fixo mensal de cabeçalho em R$, se FIXO MENSAL",
    )

    # Temporalidade
    valid_from: date | None = Field(
        default=None, description="data início validade do contrato"
    )
    valid_to: date | None = Field(
        default=None, description="data fim validade do contrato"
    )

    # LPU items (pode ter 0..N linhas)
    lpu_items: list[ExtractedLPUItem] = Field(
        default_factory=list,
        description="Lista de Preços Unitários extraída da tabela do contrato",
    )

    def confidence_per_field(self) -> dict[str, float]:
        """Heurística simples de confiança por campo:
            1.0 se preenchido, 0.0 se None/vazio.

        Permite o template HITL destacar visualmente quais campos
        precisam de revisão manual. Instructor real pode entregar
        confiança calibrada — aqui é o piso.
        """
        out: dict[str, float] = {}
        for name in (
            "empreiteira_nome", "empreiteira_cnpj", "contratante_cnpj",
            "objeto_contrato", "categoria", "tecnologia", "atividade",
            "val_fix_cab", "valid_from", "valid_to",
        ):
            v = getattr(self, name)
            out[name] = 1.0 if v not in (None, "", []) else 0.0
        out["uf"] = 1.0 if self.uf else 0.0
        out["cidade"] = 1.0 if self.cidade else 0.0
        out["lpu_items"] = 1.0 if self.lpu_items else 0.0
        return out

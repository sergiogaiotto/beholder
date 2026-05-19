"""Cliente LLM injetável pra extração R7 (Fase 4).

Define um Protocol `LLMExtractionClient` que o pipeline consome. As impls
são:

  - `MaritacaExtractionClient` (prod): chama Maritaca sabia-4 via API
    OpenAI-compatible com Instructor pra coerção pro schema Pydantic.
    Não instanciado em test (`MARITACA_API_KEY` ausente → falha
    construtor — fail-fast).
  - `MockExtractionClient` (test): devolve `ExtractedContractFields`
    pré-fabricado. Determinístico, zero dependência externa.

Tradeoff: usar Protocol em vez de ABC mantém a impl async e tipável sem
herança. Mocks tests podem ser feitos com simples funções.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from app.core.services.payments.extraction.schemas import ExtractedContractFields


@dataclass(frozen=True)
class ExtractionResult:
    """Resultado de 1 chamada de extração — payload + custo + modelo usado."""
    fields: ExtractedContractFields
    cost_brl: Decimal
    llm_model_used: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMExtractionClient(Protocol):
    """Contrato: PDF text → ExtractionResult.

    Implementações concretas em `_maritaca.py` (prod) ou neste módulo
    (`MockExtractionClient` pra tests).
    """

    async def extract(
        self,
        *,
        pdf_text: str,
        pdf_filename: str,
    ) -> ExtractionResult:
        """Extrai folha de rosto + LPU items do texto do PDF."""
        ...


class MockExtractionClient:
    """Cliente fake pra tests — devolve um payload determinístico.

    Recebe o `ExtractedContractFields` esperado no construtor pra que
    cada teste plante o caminho que quer simular (caso feliz, caso vazio,
    edge case).
    """

    def __init__(
        self,
        *,
        result_fields: ExtractedContractFields | None = None,
        cost_brl: Decimal = Decimal("0.37"),
        llm_model_used: str = "mock-sabia-4",
    ) -> None:
        self._fields = result_fields or ExtractedContractFields(
            empreiteira_nome="MOCK EMPREITEIRA LTDA",
            empreiteira_cnpj="12345678000199",
            contratante_cnpj="11222333000144",
            categoria="FIXO MENSAL",
            tecnologia="FIBRA",
            val_fix_cab=Decimal("10000.00"),
        )
        self._cost = cost_brl
        self._model = llm_model_used

    async def extract(self, *, pdf_text: str, pdf_filename: str) -> ExtractionResult:
        return ExtractionResult(
            fields=self._fields,
            cost_brl=self._cost,
            llm_model_used=self._model,
            prompt_tokens=len(pdf_text) // 4,  # aproximação
            completion_tokens=200,
        )

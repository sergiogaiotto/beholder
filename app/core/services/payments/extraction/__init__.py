"""PDF extraction R7 pipeline + service (Fase 4).

Pré-C validou empiricamente Maritaca sabia-4 em 5 PDFs reais (R$0,37/PDF,
86% campos preenchidos). Esse módulo formaliza a pipeline:

  1. PDF binário → texto markdown via docling (ou pdfplumber em fallback)
  2. Texto + prompt → ExtractedContractFields via Instructor + LLM
  3. ExtractionJob populado em status='review' aguardando HITL
  4. Após `/contratos/extracao/{id}/approve`, materializa em
     ContractMaster + ContractVersion + LPUItem (não nesta fase ainda)

Cliente LLM injetável via Protocol `LLMExtractionClient` — Maritaca em
prod, MockExtractionClient em tests (não chama API real).
"""

from __future__ import annotations

from app.core.services.payments.extraction._client import (
    ExtractionResult,
    LLMExtractionClient,
    MockExtractionClient,
)
from app.core.services.payments.extraction.schemas import (
    ExtractedContractFields,
    ExtractedLPUItem,
)

__all__ = [
    "ExtractedContractFields",
    "ExtractedLPUItem",
    "ExtractionResult",
    "LLMExtractionClient",
    "MockExtractionClient",
]

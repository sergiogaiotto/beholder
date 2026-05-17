"""Fábrica que monta o conjunto de clientes LLM disponíveis.

Stack do Beholder: ClaroHub (on-prem, OpenAI-compatible) + Maritaca (cloud).
Sem chave configurada, o adapter cai em MockLLMClient (dev offline).
"""

from __future__ import annotations

from app.adapters.llm.claro_hub_adapter import ClaroHubClient
from app.adapters.llm.maritaca_adapter import MaritacaClient
from app.adapters.llm.mock_adapter import MockLLMClient
from app.config import get_settings
from app.core.ports.llm import LLMClient


def build_clients() -> dict[str, LLMClient]:
    settings = get_settings()
    clients: dict[str, LLMClient] = {}

    if settings.claro_hub_api_key and settings.claro_hub_endpoint:
        clients[settings.claro_hub_model] = ClaroHubClient()
    else:
        clients[settings.claro_hub_model] = MockLLMClient(
            name=settings.claro_hub_model,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
        )

    if settings.maritaca_api_key:
        clients[settings.maritaca_model] = MaritacaClient()
    else:
        clients[settings.maritaca_model] = MockLLMClient(
            name=settings.maritaca_model,
            cost_per_1k_input=0.0008,
            cost_per_1k_output=0.0024,
        )

    return clients

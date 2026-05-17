"""Fábrica que monta o conjunto de clientes LLM disponíveis."""

from __future__ import annotations

from app.adapters.llm.claro_hub_adapter import ClaroHubClient
from app.adapters.llm.gaia_adapter import GaiaClient
from app.adapters.llm.maritaca_adapter import MaritacaClient
from app.adapters.llm.mock_adapter import MockLLMClient
from app.config import get_settings
from app.core.ports.llm import LLMClient


def build_clients() -> dict[str, LLMClient]:
    settings = get_settings()
    clients: dict[str, LLMClient] = {}

    if settings.azure_openai_api_key and settings.azure_openai_endpoint:
        clients[settings.azure_openai_deployment] = ClaroHubClient()
    else:
        clients[settings.azure_openai_deployment] = MockLLMClient(
            name=settings.azure_openai_deployment,
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

    if settings.gaia_api_key and settings.gaia_base_url:
        clients[settings.gaia_model] = GaiaClient()
    else:
        clients[settings.gaia_model] = MockLLMClient(
            name=settings.gaia_model,
            cost_per_1k_input=0.0001,
            cost_per_1k_output=0.0003,
        )

    return clients

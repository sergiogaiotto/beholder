"""Adaptador para o Hub GPU interno da Claro.

Endpoint OpenAI-compatible: https://hub-gpus.claro.com.br/gpt20/v1
Modelo: openai/gpt-oss-20b (reasoning model — requer max_tokens alto)
Proxy obrigatório: http://netproxy.netservicos.corp:8080
"""

from __future__ import annotations

from app.config import get_settings
from app.core.ports.llm import LLMClient, LLMResponse


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class ClaroHubClient(LLMClient):
    """Cliente para o Hub GPU interno da Claro (OpenAI-compatible)."""

    name = "openai/gpt-oss-20b"
    cost_per_1k_input = 0.0
    cost_per_1k_output = 0.0
    cost_per_1k_cached_input = 0.0

    def __init__(self):
        s = get_settings()
        self.api_key = s.azure_openai_api_key
        self.base_url = s.azure_openai_endpoint.rstrip("/") + "/v1"
        self.model = s.azure_openai_deployment
        self.proxy = s.https_proxy or s.http_proxy or None

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
        force_json: bool = False,
    ) -> LLMResponse:
        try:
            import httpx
            from openai import AsyncOpenAI
        except ImportError as e:
            raise RuntimeError("openai e httpx são necessários.") from e

        http_client = httpx.AsyncClient(
            proxy=self.proxy,
            verify=False,
        )

        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            http_client=http_client,
        )

        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if force_json:
            kwargs["response_format"] = {"type": "json_object"}

        resp = await client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        # Reasoning model: content pode vir null se max_tokens for pequeno.
        # Fallback para reasoning_content se content for None.
        text = msg.content
        if not text and hasattr(msg, "model_extra") and msg.model_extra:
            text = msg.model_extra.get("reasoning_content", "") or ""

        usage = resp.usage
        ti = usage.prompt_tokens if usage else _approx_tokens(system_prompt + user_prompt)
        to = usage.completion_tokens if usage else _approx_tokens(text)

        return LLMResponse(
            text=text,
            model=self.model,
            tokens_input=ti,
            tokens_output=to,
            cost_estimated=0.0,
        )

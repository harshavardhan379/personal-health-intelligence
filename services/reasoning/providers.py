"""LLM provider abstraction (YGH-83).

One interface, three swappable implementations, selected at runtime by
settings.llm_provider ("openai" | "grok" | "ollama"). Nothing above this file
(answer generation, the API route) ever imports a specific provider directly —
only get_llm_provider(). This is what makes M11b's eventual self-hosted
fine-tuned model a fourth implementation here, not a rewrite of the assistant.
"""

import logging
from typing import Optional, Protocol

import httpx

from services.api.app.core.config import settings

logger = logging.getLogger("app.reasoning.providers")


class LLMProviderError(Exception):
    """Raised when a provider fails to produce a response, for any reason
    (network error, bad API key, timeout, malformed response)."""


class LLMProvider(Protocol):
    name: str

    def generate(self, messages: list[dict]) -> str: ...


class OpenAIProvider:
    """Covers both OpenAI itself and, with base_url overridden, any
    OpenAI-compatible API (used for GrokProvider below)."""

    def __init__(
        self, api_key: str, model: str, base_url: Optional[str] = None, name: str = "openai"
    ):
        if not api_key:
            raise LLMProviderError(
                f"{name} provider selected but no API key is configured "
                f"(set it in services/api/.env, never in chat or source control)"
            )
        from openai import OpenAI  # imported lazily so other providers don't need this installed

        self.name = name
        self.model = model
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=settings.llm_timeout_seconds
        )

    def generate(self, messages: list[dict]) -> str:
        try:
            response = self._client.chat.completions.create(model=self.model, messages=messages)
        except Exception as exc:  # noqa: BLE001 — any SDK/network failure becomes one clear error
            raise LLMProviderError(f"{self.name} request failed: {exc}") from exc

        content = response.choices[0].message.content
        if not content:
            raise LLMProviderError(f"{self.name} returned an empty response")
        return content


class GrokProvider(OpenAIProvider):
    """xAI's Grok API is OpenAI-compatible — same client, different base_url/model."""

    def __init__(self, api_key: str, model: str):
        super().__init__(api_key=api_key, model=model, base_url="https://api.x.ai/v1", name="grok")


class OllamaProvider:
    """Local model, no API key. Requires `ollama serve` running and the model pulled."""

    name = "ollama"

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, messages: list[dict]) -> str:
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": messages, "stream": False},
                timeout=settings.llm_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                f"ollama request failed ({exc}) — is `ollama serve` running "
                f"and is '{self.model}' pulled (`ollama pull {self.model}`)?"
            ) from exc

        content = response.json().get("message", {}).get("content")
        if not content:
            raise LLMProviderError("ollama returned an empty response")
        return content


def get_llm_provider() -> LLMProvider:
    provider = settings.llm_provider.lower()

    if provider == "openai":
        return OpenAIProvider(api_key=settings.openai_api_key, model=settings.openai_model)
    if provider == "grok":
        return GrokProvider(api_key=settings.xai_api_key, model=settings.xai_model)
    if provider == "ollama":
        return OllamaProvider(base_url=settings.ollama_base_url, model=settings.ollama_model)

    raise LLMProviderError(
        f"Unknown LLM_PROVIDER '{provider}' — expected 'openai', 'grok', or 'ollama'"
    )

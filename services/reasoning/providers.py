"""LLM provider abstraction (YGH-83).

One interface, three swappable implementations, selected at runtime by
settings.llm_provider ("openai" | "grok" | "ollama"). Nothing above this file
(answer generation, the API route) ever imports a specific provider directly —
only get_llm_provider(). This is what makes M11b's eventual self-hosted
fine-tuned model a fourth implementation here, not a rewrite of the assistant.
"""

import logging
import time
from typing import Optional, Protocol

import httpx

from packages.observability import resilience
from packages.observability.context import HEADER, get_request_id
from services.api.app.core.config import settings

logger = logging.getLogger("app.reasoning.providers")


class LLMProviderError(Exception):
    """Raised when a provider fails to produce a response, for any reason
    (network error, bad API key, timeout, malformed response)."""


class LLMProvider(Protocol):
    name: str

    def generate(self, messages: list[dict]) -> str: ...


def _sleep(seconds: float) -> None:  # a function of its own so tests can skip the waiting
    time.sleep(seconds)


def generate_with_usage(provider, messages: list[dict]) -> tuple:
    """(text, usage) where usage has input_tokens/output_tokens when the provider reports them.
    A provider that only has generate() (a test stand-in, a future model) reports none."""
    if hasattr(provider, "generate_with_usage"):
        return provider.generate_with_usage(messages)
    return provider.generate(messages), {}


def _outgoing_headers() -> dict:
    """The request id goes with every call to the model, so its log line and ours can be matched."""
    request_id = get_request_id()
    return {HEADER: request_id} if request_id else {}


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
        # The SDK retries connection errors and 429/5xx itself, with backoff: the llm policy's
        # attempts, counted as tries (the SDK counts retries).
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=settings.llm_timeout_seconds,
            max_retries=resilience.POLICIES["llm"].retry.attempts - 1,
        )

    def generate(self, messages: list[dict]) -> str:
        return self.generate_with_usage(messages)[0]

    def generate_with_usage(self, messages: list[dict]) -> tuple:
        try:
            # temperature=0: reliable schema-following, not creativity.
            # response_format=json_object: constrained decoding — the API
            # guarantees syntactically valid JSON, rather than us catching
            # malformed JSON after the fact. Found necessary via YGH-86 testing:
            # a more complex nested schema (multiple claims, multiple citations
            # each) produced genuinely broken JSON (not just trailing garbage)
            # that even a lenient parser couldn't recover. OpenAI and xAI's
            # Grok API (OpenAI-compatible) both support this parameter.
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
                extra_headers=_outgoing_headers(),
            )
        except Exception as exc:  # noqa: BLE001 — any SDK/network failure becomes one clear error
            raise LLMProviderError(f"{self.name} request failed: {exc}") from exc

        content = response.choices[0].message.content
        if not content:
            raise LLMProviderError(f"{self.name} returned an empty response")
        usage = getattr(response, "usage", None)
        return content, {
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
        }


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
        return self.generate_with_usage(messages)[0]

    def _post(self, messages: list[dict]) -> httpx.Response:
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": "json",  # constrained decoding — see OpenAIProvider.generate
                "options": {"temperature": 0},
            },
            headers=_outgoing_headers(),
            # Fail fast when nothing is listening (so a retry can happen); a loaded model
            # still gets the whole llm timeout to answer. A read timeout is never retried.
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0),
        )
        response.raise_for_status()
        return response

    def generate_with_usage(self, messages: list[dict]) -> tuple:
        try:
            response = resilience.call_with_retry(
                lambda: self._post(messages),
                resilience.POLICIES["llm"].retry,
                is_transient=resilience.is_transient_http,
                name="llm",
                sleep=_sleep,
            )
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                f"ollama request failed ({exc}) — is `ollama serve` running "
                f"and is '{self.model}' pulled (`ollama pull {self.model}`)?"
            ) from exc

        body = response.json()
        content = body.get("message", {}).get("content")
        if not content:
            raise LLMProviderError("ollama returned an empty response")
        return content, {
            "input_tokens": body.get("prompt_eval_count"),
            "output_tokens": body.get("eval_count"),
        }


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

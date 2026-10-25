"""LLM providers (YGH-92, 94): token usage, the request id on outgoing calls, and retry/timeout
policy. The network is replaced by a scripted httpx.post; nothing waits."""

from types import SimpleNamespace

import httpx
import pytest

from packages.observability.context import set_request_id
from services.reasoning import providers
from services.reasoning.providers import (
    LLMProviderError,
    OllamaProvider,
    OpenAIProvider,
    generate_with_usage,
)

MESSAGES = [{"role": "user", "content": "q"}]
OK_BODY = {"message": {"content": '{"answer": "a"}'}, "prompt_eval_count": 123, "eval_count": 17}


def reply(status=200, body=None):
    request = httpx.Request("POST", "http://ollama/api/chat")
    return httpx.Response(status, json=body if body is not None else OK_BODY, request=request)


class Script:
    """A scripted httpx.post: each call returns or raises the next item."""

    def __init__(self, *items):
        self.items, self.calls = list(items), []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def ollama(monkeypatch):
    sleeps = []
    monkeypatch.setattr(providers, "_sleep", sleeps.append)
    set_request_id(None)

    def make(*items):
        script = Script(*items)
        monkeypatch.setattr(providers.httpx, "post", script)
        return OllamaProvider("http://ollama:11434/", "llama3.2:3b"), script, sleeps
    yield make
    set_request_id(None)


def test_the_text_and_the_token_counts_come_back(ollama):
    provider, script, _ = ollama(reply())
    text, usage = provider.generate_with_usage(MESSAGES)
    assert text == '{"answer": "a"}'
    assert usage == {"input_tokens": 123, "output_tokens": 17}
    assert script.calls[0][0] == "http://ollama:11434/api/chat"


def test_generate_still_returns_just_the_text(ollama):
    provider, *_ = ollama(reply())
    assert provider.generate(MESSAGES) == '{"answer": "a"}'


def test_a_provider_that_only_has_generate_reports_no_usage():
    class Plain:
        name = "plain"

        def generate(self, messages):
            return "text"

    assert generate_with_usage(Plain(), MESSAGES) == ("text", {})


def test_a_provider_that_reports_usage_is_asked_for_it():
    class Reporting:
        name = "reporting"

        def generate(self, messages):
            return "wrong: the plain path was taken"

        def generate_with_usage(self, messages):
            return "text", {"input_tokens": 7, "output_tokens": 3}

    assert generate_with_usage(Reporting(), MESSAGES) == (
        "text", {"input_tokens": 7, "output_tokens": 3})


def test_missing_counts_are_none_not_an_error(ollama):
    provider, *_ = ollama(reply(body={"message": {"content": "x"}}))
    assert provider.generate_with_usage(MESSAGES)[1] == {"input_tokens": None,
                                                         "output_tokens": None}


def test_the_request_id_goes_with_the_call_to_the_model(ollama):
    provider, script, _ = ollama(reply())
    set_request_id("req-llm-1")
    provider.generate(MESSAGES)
    assert script.calls[0][1]["headers"] == {"X-Request-ID": "req-llm-1"}


def test_no_request_id_means_no_header(ollama):
    provider, script, _ = ollama(reply())
    provider.generate(MESSAGES)
    assert script.calls[0][1]["headers"] == {}


def test_a_refused_connection_is_retried_with_growing_waits_until_it_works(ollama):
    provider, script, sleeps = ollama(httpx.ConnectError("refused"),
                                      httpx.ConnectError("refused"), reply())
    assert provider.generate(MESSAGES) == '{"answer": "a"}'
    assert len(script.calls) == 3
    assert 1.0 <= sleeps[0] <= 1.25 and 2.0 <= sleeps[1] <= 2.5


def test_a_server_error_is_retried(ollama):
    provider, script, _ = ollama(reply(status=503), reply())
    assert provider.generate(MESSAGES)
    assert len(script.calls) == 2


def test_it_gives_up_after_three_tries_with_one_clear_error(ollama):
    provider, script, sleeps = ollama(*[httpx.ConnectError("refused")] * 3)
    with pytest.raises(LLMProviderError, match="ollama request failed"):
        provider.generate(MESSAGES)
    assert len(script.calls) == 3 and len(sleeps) == 2


def test_a_client_error_is_never_retried(ollama):
    for status in (400, 401, 404):
        provider, script, sleeps = ollama(reply(status=status))
        with pytest.raises(LLMProviderError):
            provider.generate(MESSAGES)
        assert len(script.calls) == 1 and sleeps == [], status


def test_a_slow_model_is_not_asked_again(ollama):
    provider, script, sleeps = ollama(httpx.ReadTimeout("slow"))
    with pytest.raises(LLMProviderError):
        provider.generate(MESSAGES)
    assert len(script.calls) == 1 and sleeps == []


def test_connecting_fails_fast_but_a_loaded_model_gets_the_full_wait(ollama):
    provider, script, _ = ollama(reply())
    provider.generate(MESSAGES)
    timeout = script.calls[0][1]["timeout"]
    assert timeout.connect == 10.0 and timeout.read == providers.settings.llm_timeout_seconds


def test_an_empty_reply_is_an_error(ollama):
    provider, *_ = ollama(reply(body={"message": {"content": ""}}))
    with pytest.raises(LLMProviderError, match="empty"):
        provider.generate(MESSAGES)


# --- the OpenAI-compatible provider ------------------------------------------------------
class FakeCompletions:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.kwargs = response, error, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def openai_provider(response=None, error=None):
    provider = OpenAIProvider(api_key="test-key", model="gpt-test")
    completions = FakeCompletions(response, error)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return provider, completions


def completion(content='{"answer": "a"}', usage=(50, 9)):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None if usage is None else SimpleNamespace(prompt_tokens=usage[0],
                                                         completion_tokens=usage[1]))


def test_the_sdk_retries_as_many_times_as_the_llm_policy_allows():
    provider = OpenAIProvider(api_key="test-key", model="gpt-test")
    assert provider._client.max_retries == providers.resilience.POLICIES["llm"].retry.attempts - 1


def test_openai_reports_tokens_and_sends_the_request_id():
    provider, completions = openai_provider(completion())
    set_request_id("req-openai")
    text, usage = provider.generate_with_usage(MESSAGES)
    set_request_id(None)
    assert text == '{"answer": "a"}' and usage == {"input_tokens": 50, "output_tokens": 9}
    assert completions.kwargs["extra_headers"] == {"X-Request-ID": "req-openai"}
    assert completions.kwargs["temperature"] == 0


def test_openai_without_usage_reports_none():
    provider, _ = openai_provider(completion(usage=None))
    assert provider.generate_with_usage(MESSAGES)[1] == {"input_tokens": None,
                                                         "output_tokens": None}


def test_an_openai_failure_is_one_clear_error():
    provider, _ = openai_provider(error=RuntimeError("connection reset"))
    with pytest.raises(LLMProviderError, match="request failed"):
        provider.generate(MESSAGES)


def test_an_empty_openai_reply_is_an_error():
    provider, _ = openai_provider(completion(content=""))
    with pytest.raises(LLMProviderError, match="empty"):
        provider.generate(MESSAGES)

"""Tests for papercast.llm.client — provider abstraction.

Covers:
    - LLMSpec construction & key resolution
    - build_provider factory dispatch
    - AnthropicProvider call shape, retry, response extraction
    - OpenAIProvider HTTP call shape, retry on 429/5xx, response extraction
    - PRESETS shape (front-end picker contract)
    - Backwards-compat aliases (AnthropicLLM / AnthropicConfig)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from papercast.llm.client import (
    PRESETS,
    AnthropicConfig,
    AnthropicLLM,
    AnthropicProvider,
    LLMError,
    LLMNotConfiguredError,
    LLMSpec,
    OpenAIProvider,
    _is_retryable_default,
    build_provider,
)


# ---------------------------------------------------------------------------
# Anthropic test stubs
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, parts: list[str]) -> None:
        self.content = [_FakeBlock(p) for p in parts]


class _FakeMessages:
    """Records calls and replays canned responses."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: list[Any] = []

    def queue(self, *responses: Any) -> None:
        self.responses.extend(responses)

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.responses:
            return _FakeMessage(["ok"])
        head = self.responses.pop(0)
        if isinstance(head, Exception):
            raise head
        return head


class _FakeAnthropicClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


class _FakeRateLimitError(Exception):
    """Mimics anthropic.RateLimitError for retry-classification tests."""


_FakeRateLimitError.__name__ = "RateLimitError"


class _FakeBadRequestError(Exception):
    """Non-retryable; mimics anthropic.BadRequestError."""


_FakeBadRequestError.__name__ = "BadRequestError"


def _spec_no_key_lookup(**overrides: Any) -> LLMSpec:
    """Build an LLMSpec that won't trigger the env-var-resolution path
    (provider construction skips that when client/http_client is injected)."""
    base = {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "api_key_env": "ANTHROPIC_API_KEY",
        "max_tokens": 8000,
        "backoff_sec": (0.0,),
    }
    base.update(overrides)
    return LLMSpec(**base)


# ---------------------------------------------------------------------------
# AnthropicProvider — call shape & retry
# ---------------------------------------------------------------------------


def test_anthropic_complete_passes_prompt_through() -> None:
    fake = _FakeAnthropicClient()
    fake.messages.queue(_FakeMessage(["here is the answer"]))
    p = AnthropicProvider(_spec_no_key_lookup(), client=fake)

    out = p.complete("ping")

    assert out == "here is the answer"
    call = fake.messages.calls[0]
    assert call["messages"] == [{"role": "user", "content": "ping"}]
    assert call["model"] == "claude-sonnet-4-6"
    assert call["max_tokens"] == 8000


def test_anthropic_system_prompt_when_set() -> None:
    fake = _FakeAnthropicClient()
    fake.messages.queue(_FakeMessage(["ok"]))
    p = AnthropicProvider(_spec_no_key_lookup(system_prompt="be terse"), client=fake)

    p.complete("hello")

    assert fake.messages.calls[0]["system"] == "be terse"


def test_anthropic_temperature_when_set() -> None:
    fake = _FakeAnthropicClient()
    fake.messages.queue(_FakeMessage(["ok"]))
    p = AnthropicProvider(_spec_no_key_lookup(temperature=0.3), client=fake)
    p.complete("hi")
    assert fake.messages.calls[0]["temperature"] == 0.3


def test_anthropic_extra_params_merged() -> None:
    fake = _FakeAnthropicClient()
    fake.messages.queue(_FakeMessage(["ok"]))
    p = AnthropicProvider(
        _spec_no_key_lookup(extra_params={"top_p": 0.9, "metadata": {"user": "u1"}}),
        client=fake,
    )
    p.complete("hi")
    call = fake.messages.calls[0]
    assert call["top_p"] == 0.9
    assert call["metadata"] == {"user": "u1"}


def test_empty_prompt_rejected() -> None:
    p = AnthropicProvider(_spec_no_key_lookup(), client=_FakeAnthropicClient())
    with pytest.raises(ValueError):
        p.complete("")


def test_anthropic_missing_api_key_raises_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMNotConfiguredError):
        AnthropicProvider(LLMSpec())  # no client → triggers env lookup


def test_retries_on_transient_then_succeeds() -> None:
    fake = _FakeAnthropicClient()
    fake.messages.queue(
        _FakeRateLimitError("slow"),
        _FakeRateLimitError("still"),
        _FakeMessage(["finally"]),
    )
    sleeps: list[float] = []
    p = AnthropicProvider(
        _spec_no_key_lookup(backoff_sec=(0.0, 0.0, 0.0)),
        client=fake,
        sleep=sleeps.append,
    )
    assert p.complete("ping") == "finally"
    assert len(fake.messages.calls) == 3
    assert len(sleeps) == 2


def test_does_not_retry_on_non_retryable_error() -> None:
    fake = _FakeAnthropicClient()
    fake.messages.queue(_FakeBadRequestError("bad prompt"))
    sleeps: list[float] = []
    p = AnthropicProvider(_spec_no_key_lookup(), client=fake, sleep=sleeps.append)

    with pytest.raises(LLMError):
        p.complete("ping")
    assert len(fake.messages.calls) == 1
    assert sleeps == []


def test_exhausts_retries_then_raises() -> None:
    fake = _FakeAnthropicClient()
    fake.messages.queue(_FakeRateLimitError("nope"), _FakeRateLimitError("nope"))
    p = AnthropicProvider(
        _spec_no_key_lookup(backoff_sec=(0.0,)),
        client=fake,
        sleep=lambda _s: None,
    )
    with pytest.raises(LLMError):
        p.complete("ping")
    assert len(fake.messages.calls) == 2


def test_anthropic_concatenates_text_blocks() -> None:
    fake = _FakeAnthropicClient()
    fake.messages.queue(_FakeMessage(["first", " second"]))
    p = AnthropicProvider(_spec_no_key_lookup(), client=fake)
    assert p.complete("ping") == "first second"


def test_anthropic_response_with_no_text_raises() -> None:
    class EmptyMsg:
        content = []

    fake = _FakeAnthropicClient()
    fake.messages.queue(EmptyMsg())
    p = AnthropicProvider(_spec_no_key_lookup(), client=fake)
    with pytest.raises(LLMError):
        p.complete("ping")


def test_anthropic_string_response_tolerated() -> None:
    fake = MagicMock()
    fake.messages.create.return_value = "raw text"
    p = AnthropicProvider(_spec_no_key_lookup(), client=fake)
    assert p.complete("ping") == "raw text"


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


class _FakeHTTPResponse:
    def __init__(self, status_code: int, body: dict[str, Any] | str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if isinstance(body, dict) else body

    def json(self) -> Any:
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("not json")


class _FakeHTTPClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: list[Any] = []

    def queue(self, *responses: Any) -> None:
        self.responses.extend(responses)

    def post(self, url: str, *, json: Any) -> Any:  # noqa: A002 — mirror httpx
        self.calls.append({"url": url, "json": json})
        if not self.responses:
            return _FakeHTTPResponse(200, {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            })
        head = self.responses.pop(0)
        if isinstance(head, Exception):
            raise head
        return head


def _openai_spec(**overrides: Any) -> LLMSpec:
    base = {
        "provider": "openai",
        "model": "gpt-4.1",
        "api_key": "test-key",
        "base_url": "https://api.openai.com/v1",
        "max_tokens": 1024,
        "backoff_sec": (0.0,),
    }
    base.update(overrides)
    return LLMSpec(**base)


def test_openai_call_shape() -> None:
    http = _FakeHTTPClient()
    http.queue(_FakeHTTPResponse(200, {
        "choices": [{"message": {"role": "assistant", "content": "hi back"}}],
    }))
    p = OpenAIProvider(_openai_spec(), http_client=http)

    out = p.complete("hello")

    assert out == "hi back"
    assert http.calls[0]["url"] == "https://api.openai.com/v1/chat/completions"
    body = http.calls[0]["json"]
    assert body["model"] == "gpt-4.1"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["max_tokens"] == 1024


def test_openai_system_prompt_prepended() -> None:
    http = _FakeHTTPClient()
    p = OpenAIProvider(_openai_spec(system_prompt="be terse"), http_client=http)
    p.complete("hi")
    body = http.calls[0]["json"]
    assert body["messages"][0] == {"role": "system", "content": "be terse"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}


def test_openai_temperature_and_extra_params_merged() -> None:
    http = _FakeHTTPClient()
    p = OpenAIProvider(
        _openai_spec(temperature=0.7, extra_params={"frequency_penalty": 0.1}),
        http_client=http,
    )
    p.complete("hi")
    body = http.calls[0]["json"]
    assert body["temperature"] == 0.7
    assert body["frequency_penalty"] == 0.1


def test_openai_retries_429() -> None:
    http = _FakeHTTPClient()
    http.queue(
        _FakeHTTPResponse(429, "rate limited"),
        _FakeHTTPResponse(200, {
            "choices": [{"message": {"role": "assistant", "content": "ok now"}}],
        }),
    )
    sleeps: list[float] = []
    p = OpenAIProvider(_openai_spec(backoff_sec=(0.0,)), http_client=http, sleep=sleeps.append)
    assert p.complete("ping") == "ok now"
    assert len(http.calls) == 2


def test_openai_retries_5xx() -> None:
    http = _FakeHTTPClient()
    http.queue(
        _FakeHTTPResponse(503, "service unavailable"),
        _FakeHTTPResponse(200, {
            "choices": [{"message": {"role": "assistant", "content": "back"}}],
        }),
    )
    p = OpenAIProvider(_openai_spec(backoff_sec=(0.0,)), http_client=http, sleep=lambda _s: None)
    assert p.complete("ping") == "back"
    assert len(http.calls) == 2


def test_openai_does_not_retry_4xx() -> None:
    http = _FakeHTTPClient()
    http.queue(_FakeHTTPResponse(400, "bad request"))
    p = OpenAIProvider(_openai_spec(backoff_sec=(0.0,)), http_client=http, sleep=lambda _s: None)
    with pytest.raises(LLMError):
        p.complete("ping")
    assert len(http.calls) == 1


def test_openai_handles_content_array_form() -> None:
    """Some OpenAI-compatible servers (e.g. with tool use) return content as
    a list of typed parts."""
    http = _FakeHTTPClient()
    http.queue(_FakeHTTPResponse(200, {
        "choices": [{"message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "first "},
                {"type": "text", "text": "second"},
            ],
        }}],
    }))
    p = OpenAIProvider(_openai_spec(), http_client=http)
    assert p.complete("ping") == "first second"


def test_openai_missing_choices_raises() -> None:
    http = _FakeHTTPClient()
    http.queue(_FakeHTTPResponse(200, {"choices": []}))
    p = OpenAIProvider(_openai_spec(backoff_sec=(0.0,)), http_client=http)
    with pytest.raises(LLMError):
        p.complete("ping")


def test_openai_missing_api_key_for_remote_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    spec = LLMSpec(
        provider="openai",
        model="gpt-4.1",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
    )
    with pytest.raises(LLMNotConfiguredError):
        OpenAIProvider(spec)


def test_openai_localhost_tolerates_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama / LM Studio accept any non-empty bearer; we should not block."""
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    spec = LLMSpec(
        provider="openai_compat",
        model="qwen3:32b",
        base_url="http://localhost:11434/v1",
        api_key_env="OLLAMA_API_KEY",
    )
    # Should construct without raising. We pass a fake http client to avoid
    # a real network call.
    http = _FakeHTTPClient()
    p = OpenAIProvider(spec, http_client=http)
    p.complete("hello")
    assert http.calls  # request actually went out


# ---------------------------------------------------------------------------
# build_provider factory
# ---------------------------------------------------------------------------


def test_build_provider_dispatches_to_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    # We cannot easily build a real AnthropicProvider without the SDK, so just
    # check the factory recognises the provider string and bubbles construction
    # errors through. Since SDK may or may not be installed, we trap both
    # outcomes — the important contract is: it does NOT raise "unknown provider".
    spec = LLMSpec(provider="anthropic", model="claude-sonnet-4-6", api_key="fake")
    try:
        provider = build_provider(spec)
        from papercast.llm.client import AnthropicProvider as _AP
        assert isinstance(provider, _AP)
    except LLMNotConfiguredError as e:
        # SDK missing in this env — that's fine, we just verify the message
        # is the SDK message, not the unknown-provider message.
        assert "anthropic SDK" in str(e) or "missing api key" in str(e).lower()


def test_build_provider_dispatches_to_openai() -> None:
    spec = LLMSpec(
        provider="openai",
        model="gpt-4.1",
        api_key="fake",
        base_url="https://api.openai.com/v1",
    )
    p = build_provider(spec)
    assert isinstance(p, OpenAIProvider)


def test_build_provider_dispatches_to_openai_compat() -> None:
    spec = LLMSpec(
        provider="openai_compat",
        model="deepseek-chat",
        api_key="fake",
        base_url="https://api.deepseek.com/v1",
    )
    p = build_provider(spec)
    assert isinstance(p, OpenAIProvider)


def test_build_provider_rejects_unknown() -> None:
    spec = LLMSpec(provider="not-a-thing")
    with pytest.raises(LLMNotConfiguredError):
        build_provider(spec)


# ---------------------------------------------------------------------------
# LLMSpec
# ---------------------------------------------------------------------------


def test_spec_resolves_explicit_api_key() -> None:
    spec = LLMSpec(provider="openai", api_key="explicit", api_key_env="UNUSED")
    assert spec.resolved_api_key() == "explicit"


def test_spec_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "from-env")
    spec = LLMSpec(api_key_env="MY_KEY")
    assert spec.resolved_api_key() == "from-env"


def test_spec_returns_none_when_neither_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZZZ_NOPE", raising=False)
    spec = LLMSpec(api_key_env="ZZZ_NOPE")
    assert spec.resolved_api_key() is None


# ---------------------------------------------------------------------------
# PRESETS — front-end picker contract
# ---------------------------------------------------------------------------


def test_presets_contain_required_providers() -> None:
    for name in ("anthropic", "openai", "deepseek", "qwen", "ollama", "custom_openai", "custom_anthropic"):
        assert name in PRESETS, f"preset {name!r} missing"


def test_each_preset_has_required_fields() -> None:
    for name, preset in PRESETS.items():
        for key in ("label", "provider", "api_key_env", "model_examples"):
            assert key in preset, f"preset {name!r} missing field {key!r}"
        assert preset["provider"] in ("anthropic", "openai", "openai_compat")


# ---------------------------------------------------------------------------
# Backwards compatibility
# ---------------------------------------------------------------------------


def test_anthropic_llm_alias_still_works() -> None:
    """The old `AnthropicLLM` and `AnthropicConfig` names still resolve to the new ones."""
    assert AnthropicLLM is AnthropicProvider
    assert AnthropicConfig is LLMSpec


# ---------------------------------------------------------------------------
# Retry classification helper
# ---------------------------------------------------------------------------


def test_default_retryable_recognises_common_classes() -> None:
    assert _is_retryable_default(_FakeRateLimitError("x"))
    assert _is_retryable_default(ConnectionError("x"))
    assert _is_retryable_default(TimeoutError("x"))
    assert not _is_retryable_default(ValueError("x"))

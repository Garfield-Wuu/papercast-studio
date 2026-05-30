"""LLM provider abstraction.

Why a provider layer:
    The Reader / Planner / Scripter only need a single call:
        provider.complete(prompt) -> str
    They should not know whether the bytes go to api.anthropic.com,
    api.openai.com, a self-hosted vLLM, or DashScope. CC-SWITCH popularised
    this pattern — store provider+base_url+api_key+model as data, swap at
    runtime — and it is the cleanest fit for our use case where the WebUI
    will let the lab manager pick a provider per project.

Layers:
    LLMProvider             Protocol — anything with `.complete(prompt)`
    BaseProvider            shared retry + error classification
    AnthropicProvider       wraps anthropic SDK; supports custom base_url
                            (Claude API forwarders / proxies all set this)
    OpenAIProvider          plain httpx call to /v1/chat/completions;
                            covers OpenAI, DeepSeek, Moonshot, Qwen
                            DashScope-compatible, Ollama, vLLM, LM Studio,
                            and anything else exposing the OpenAI Chat
                            Completion schema.
    LLMSpec                 frozen dataclass — the unit of configuration
    build_provider(spec)    spec -> provider instance (the factory).
    PRESETS                 tiny catalogue of out-of-the-box base_url
                            values for the front-end picker.

Errors are deliberately string-classified (`type(exc).__name__`) so we
keep the anthropic SDK as a soft dependency and don't import its
exception classes here.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LLMError(RuntimeError):
    """Any error originating from the LLM call (after retries exhausted)."""


class LLMNotConfiguredError(LLMError):
    """Construction-time failure — missing api key, missing SDK, bad provider name."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class LLMProvider(Protocol):
    """Narrow surface every consumer of the LLM stage relies on."""

    def complete(self, prompt: str) -> str: ...


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


# Default backoff schedule for retryable failures (seconds).
_BACKOFF_SCHEDULE: tuple[float, ...] = (1.0, 3.0, 8.0)


@dataclass(frozen=True)
class LLMSpec:
    """Everything needed to construct a provider, in one immutable record.

    The same shape is what the WebUI form will POST to
    `PUT /api/config` — keep it close to plain JSON / YAML so the round
    trip is lossless.
    """

    provider: str = "anthropic"           # one of: anthropic, openai, openai_compat
    model: str = "claude-sonnet-4-6"
    api_key: str | None = None            # explicit key; if None we resolve api_key_env
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str | None = None           # None → use provider default
    max_tokens: int = 8000
    temperature: float | None = None      # None → don't send the field
    timeout_sec: float = 90.0
    system_prompt: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_params: dict[str, Any] = field(default_factory=dict)  # passed straight into the request body
    backoff_sec: tuple[float, ...] = _BACKOFF_SCHEDULE

    def resolved_api_key(self) -> str | None:
        """Return the literal api_key, falling back to the named env var."""
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


# Front-end picker presets — a curated list of OpenAI-compatible endpoints.
# Not exhaustive; users can always type a custom base_url in the WebUI.
PRESETS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "label": "Anthropic Claude",
        "provider": "anthropic",
        "base_url": None,                      # SDK default: https://api.anthropic.com
        "api_key_env": "ANTHROPIC_API_KEY",
        "model_examples": ["claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5"],
    },
    "openai": {
        "label": "OpenAI",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "model_examples": ["gpt-5", "gpt-5-mini", "gpt-4.1"],
    },
    "deepseek": {
        "label": "DeepSeek",
        "provider": "openai_compat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_examples": ["deepseek-chat", "deepseek-reasoner"],
    },
    "moonshot": {
        "label": "Moonshot Kimi",
        "provider": "openai_compat",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "model_examples": ["moonshot-v1-32k", "moonshot-v1-128k"],
    },
    "qwen": {
        "label": "Qwen (DashScope OpenAI 兼容)",
        "provider": "openai_compat",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "model_examples": ["qwen-max", "qwen-plus", "qwen-turbo"],
    },
    "zhipu": {
        "label": "智谱 GLM",
        "provider": "openai_compat",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "model_examples": ["glm-4.6", "glm-4-plus", "glm-4-air"],
    },
    "ollama": {
        "label": "Ollama (本地)",
        "provider": "openai_compat",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "OLLAMA_API_KEY",       # any non-empty value; Ollama ignores
        "model_examples": ["qwen3:32b", "llama3.2:latest"],
    },
    "vllm": {
        "label": "vLLM / LM Studio (本地或自托管)",
        "provider": "openai_compat",
        "base_url": "http://localhost:8000/v1",
        "api_key_env": "VLLM_API_KEY",
        "model_examples": ["meta-llama/Llama-3.1-70B-Instruct"],
    },
    "custom_openai": {
        "label": "自定义 OpenAI 兼容端点",
        "provider": "openai_compat",
        "base_url": None,                       # user supplies
        "api_key_env": "OPENAI_API_KEY",
        "model_examples": [],
    },
    "custom_anthropic": {
        "label": "自定义 Anthropic 兼容端点 (Claude 中转)",
        "provider": "anthropic",
        "base_url": None,                       # user supplies
        "api_key_env": "ANTHROPIC_API_KEY",
        "model_examples": ["claude-sonnet-4-6"],
    },
}


# ---------------------------------------------------------------------------
# Base provider — retry + error classification
# ---------------------------------------------------------------------------


class BaseProvider:
    """Shared retry / sleep / error-shape logic for every concrete provider.

    Subclasses implement `_call_once(prompt) -> str` and (optionally)
    override `_is_retryable(exc)` to recognise their SDK-specific
    transient errors.
    """

    def __init__(
        self,
        spec: LLMSpec,
        *,
        sleep: Any = time.sleep,
    ) -> None:
        self._spec = spec
        self._sleep = sleep

    @property
    def spec(self) -> LLMSpec:
        return self._spec

    # Public API --------------------------------------------------------

    def complete(self, prompt: str) -> str:
        if not prompt or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")

        backoff = self._spec.backoff_sec
        attempts = len(backoff) + 1
        last_err: Exception | None = None
        for i in range(attempts):
            try:
                return self._call_once(prompt)
            except Exception as e:  # noqa: BLE001 — re-classified below
                last_err = e
                if not self._is_retryable(e) or i == attempts - 1:
                    break
                wait = backoff[i]
                logger.warning(
                    "%s call failed (attempt %d/%d): %s. retrying in %.1fs",
                    type(self).__name__, i + 1, attempts, e, wait,
                )
                self._sleep(wait)

        raise LLMError(f"{type(self).__name__} failed after {attempts} attempts: {last_err}") from last_err

    # To be overridden -------------------------------------------------

    def _call_once(self, prompt: str) -> str:  # pragma: no cover — abstract
        raise NotImplementedError

    def _is_retryable(self, exc: Exception) -> bool:
        return _is_retryable_default(exc)


def _is_retryable_default(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "ConnectError",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "RemoteProtocolError",
    }:
        return True
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return False


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


class AnthropicProvider(BaseProvider):
    """Wraps the official anthropic SDK; supports custom base_url for proxies."""

    def __init__(
        self,
        spec: LLMSpec,
        *,
        client: Any = None,           # injectable for tests
        sleep: Any = time.sleep,
    ) -> None:
        super().__init__(spec, sleep=sleep)

        if client is not None:
            self._client = client
            return

        api_key = spec.resolved_api_key()
        if not api_key:
            raise LLMNotConfiguredError(
                f"missing api key for Anthropic provider — "
                f"set {spec.api_key_env!r} in env or pass api_key in the LLMSpec."
            )

        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError as e:
            raise LLMNotConfiguredError(
                "anthropic SDK not installed. Reinstall with the [llm] extra: "
                "pip install -e \".[llm]\""
            ) from e

        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": spec.timeout_sec}
        if spec.base_url:
            kwargs["base_url"] = spec.base_url
        if spec.extra_headers:
            kwargs["default_headers"] = dict(spec.extra_headers)
        self._client = anthropic.Anthropic(**kwargs)

    def _call_once(self, prompt: str) -> str:
        kwargs: dict[str, Any] = {
            "model": self._spec.model,
            "max_tokens": self._spec.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._spec.system_prompt:
            kwargs["system"] = self._spec.system_prompt
        if self._spec.temperature is not None:
            kwargs["temperature"] = self._spec.temperature
        kwargs.update(self._spec.extra_params)

        resp = self._client.messages.create(**kwargs)
        return _extract_anthropic_text(resp)


def _extract_anthropic_text(resp: Any) -> str:
    """Flatten Anthropic Messages response into a single text block."""
    content = getattr(resp, "content", None)
    if content is None:
        if isinstance(resp, str):
            return resp
        raise LLMError(f"unexpected anthropic response shape: {type(resp).__name__}")
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(text)
    if not parts:
        raise LLMError("anthropic response had no text blocks")
    return "".join(parts)


# ---------------------------------------------------------------------------
# OpenAI-compatible provider (covers OpenAI, DeepSeek, Qwen, Moonshot, Ollama, …)
# ---------------------------------------------------------------------------


class OpenAIProvider(BaseProvider):
    """Plain httpx call to `<base_url>/chat/completions`.

    Compatible with any service that follows the OpenAI Chat Completion
    schema: OpenAI itself, DeepSeek, Moonshot, Qwen DashScope, Zhipu GLM,
    Ollama, vLLM, LM Studio, OpenRouter, ...

    We intentionally do not depend on the openai SDK — keeping httpx-only
    means the OpenAI provider works without an extra `[openai]` extra and
    avoids version-skew issues across providers.
    """

    def __init__(
        self,
        spec: LLMSpec,
        *,
        http_client: Any = None,      # injectable for tests
        sleep: Any = time.sleep,
    ) -> None:
        super().__init__(spec, sleep=sleep)

        api_key = spec.resolved_api_key()
        # Local servers (Ollama, LM Studio) often accept any non-empty value.
        # We tolerate missing keys for localhost-style base URLs but warn.
        if not api_key:
            if spec.base_url and _looks_like_localhost(spec.base_url):
                logger.info(
                    "OpenAIProvider: no api key for %s; sending placeholder (localhost endpoint)",
                    spec.base_url,
                )
                api_key = "ollama"  # any non-empty token
            else:
                raise LLMNotConfiguredError(
                    f"missing api key for OpenAI-compatible provider at {spec.base_url!r} — "
                    f"set {spec.api_key_env!r} in env or pass api_key in the LLMSpec."
                )

        self._api_key = api_key
        self._base_url = (spec.base_url or "https://api.openai.com/v1").rstrip("/")

        if http_client is not None:
            self._http = http_client
            self._owns_http = False
        else:
            try:
                import httpx
            except ImportError as e:
                raise LLMNotConfiguredError("httpx not installed") from e
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **(spec.extra_headers or {}),
            }
            self._http = httpx.Client(timeout=spec.timeout_sec, headers=headers)
            self._owns_http = True

    def _call_once(self, prompt: str) -> str:
        url = f"{self._base_url}/chat/completions"
        messages = []
        if self._spec.system_prompt:
            messages.append({"role": "system", "content": self._spec.system_prompt})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self._spec.model,
            "messages": messages,
            "max_tokens": self._spec.max_tokens,
        }
        if self._spec.temperature is not None:
            body["temperature"] = self._spec.temperature
        body.update(self._spec.extra_params)

        resp = self._http.post(url, json=body)
        # httpx-style error → raise_for_status; we re-raise as classified errors
        # so retry logic kicks in for 429/5xx and stops for 4xx.
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            text = _safe_resp_text(resp)
            if status == 429 or status >= 500:
                raise _OpenAIRetryable(f"HTTP {status}: {text[:300]}")
            raise LLMError(f"HTTP {status}: {text[:500]}")

        try:
            payload = resp.json()
        except Exception as e:
            raise LLMError(f"OpenAI response not valid JSON: {e}") from e
        return _extract_openai_text(payload)

    def _is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, _OpenAIRetryable):
            return True
        return _is_retryable_default(exc)

    def __del__(self) -> None:  # pragma: no cover — best-effort cleanup
        if getattr(self, "_owns_http", False):
            try:
                self._http.close()
            except Exception:
                pass


class _OpenAIRetryable(Exception):
    """Marker for HTTP 429 / 5xx so BaseProvider retries them."""


def _extract_openai_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not choices:
        raise LLMError(f"OpenAI response missing 'choices': {json.dumps(payload)[:200]}")
    msg = choices[0].get("message", {})
    content = msg.get("content")
    if isinstance(content, str):
        return content
    # Some OpenAI-compatible providers return content as a list of parts
    # (e.g. when tool_use is mixed in). Concatenate text parts only.
    if isinstance(content, list):
        parts = [
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") in (None, "text")
        ]
        joined = "".join(parts)
        if joined:
            return joined
    raise LLMError(f"OpenAI response had no text content: {json.dumps(msg)[:200]}")


def _looks_like_localhost(url: str) -> bool:
    return any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1"))


def _safe_resp_text(resp: Any) -> str:
    try:
        return resp.text
    except Exception:
        return repr(resp)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_provider(spec: LLMSpec) -> LLMProvider:
    """Construct the right provider for `spec.provider`.

    Recognised values:
        "anthropic"      → AnthropicProvider (via official SDK)
        "openai"         → OpenAIProvider (api.openai.com default base_url)
        "openai_compat"  → OpenAIProvider (any OpenAI-schema endpoint)
    """
    p = (spec.provider or "").lower()
    if p == "anthropic":
        return AnthropicProvider(spec)
    if p in ("openai", "openai_compat"):
        return OpenAIProvider(spec)
    raise LLMNotConfiguredError(
        f"unknown provider {spec.provider!r}; expected one of "
        f"'anthropic', 'openai', 'openai_compat'."
    )


# ---------------------------------------------------------------------------
# Backwards-compat shim
# ---------------------------------------------------------------------------


# Older imports used `AnthropicLLM` and `AnthropicConfig`. Keep them
# pointing at the new types so nothing in-tree breaks.
AnthropicConfig = LLMSpec
AnthropicLLM = AnthropicProvider

"""LLM clients for the Reader / Author agents.

Provider abstraction (CC-SWITCH-style):
    LLMProvider          Protocol — anything with `.complete(prompt) -> str`
    AnthropicProvider    via anthropic SDK; supports custom base_url for proxies
    OpenAIProvider       plain httpx call to /chat/completions; covers OpenAI,
                         DeepSeek, Moonshot, Qwen, Zhipu, Ollama, vLLM, etc.
    LLMSpec              frozen dataclass — the unit of configuration that round-trips
                         losslessly to the WebUI form / config.yaml.
    build_provider(spec) factory: spec → ready-to-call provider.
    PRESETS              curated list of out-of-the-box base_url presets.

Three task-specific wrappers consume the provider:
    AnthropicPlanner / AnthropicScripter — historical names; they accept *any*
    LLMProvider, not just Anthropic. Kept as-is for backwards compatibility.
"""

from __future__ import annotations

from .client import (
    PRESETS,
    AnthropicConfig,                # alias of LLMSpec, backwards compat
    AnthropicLLM,                   # alias of AnthropicProvider, backwards compat
    AnthropicProvider,
    BaseProvider,
    LLMError,
    LLMNotConfiguredError,
    LLMProvider,
    LLMSpec,
    OpenAIProvider,
    build_provider,
)
from .planner import AnthropicPlanner, SlidesPlanner
from .scripter import AnthropicScripter, Scripter

__all__ = [
    # Provider layer
    "LLMProvider",
    "LLMSpec",
    "BaseProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "build_provider",
    "PRESETS",
    "LLMError",
    "LLMNotConfiguredError",
    # Backwards-compat aliases
    "AnthropicLLM",
    "AnthropicConfig",
    # Task-specific wrappers
    "SlidesPlanner",
    "AnthropicPlanner",
    "Scripter",
    "AnthropicScripter",
]

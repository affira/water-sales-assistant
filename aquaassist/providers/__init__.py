"""Choose the LLM backend from the LLM_PROVIDER environment variable.

    anthropic       Anthropic API (default)
    azure_openai    Azure OpenAI deployment in Microsoft Foundry (openai SDK)
    foundry_claude  Claude deployment in Microsoft Foundry (Anthropic SDK, AnthropicFoundry)

Settings are read when called, not at import, so the choice can change per process or test.
"""
from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional

from .. import config
from .anthropic_api import anthropic_from_env, foundry_claude_from_env
from .azure_openai import azure_openai_from_env
from .base import (
    AssistantTurn,
    Message,
    ModelResponse,
    Provider,
    ProviderConfigError,
    ProviderError,
    ToolCall,
    ToolResult,
    ToolResults,
    UserText,
)

DEFAULT_PROVIDER = "anthropic"

# Variables each backend cannot work without (names only; values are never logged or returned).
REQUIRED_ENV: Dict[str, List[str]] = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "azure_openai": ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_KEY"],
    "foundry_claude": ["ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_FOUNDRY_API_KEY"],
}

_FACTORIES: Dict[str, Callable[[], Provider]] = {
    "anthropic": anthropic_from_env,
    "azure_openai": azure_openai_from_env,
    "foundry_claude": foundry_claude_from_env,
}


def selected_provider() -> str:
    return (os.environ.get("LLM_PROVIDER", "") or DEFAULT_PROVIDER).strip().lower()


def configuration_problems(name: Optional[str] = None) -> List[str]:
    """Human-readable problems with the provider settings; empty when ready to use."""
    name = name or selected_provider()
    if name not in REQUIRED_ENV:
        return [f"LLM_PROVIDER must be one of {', '.join(REQUIRED_ENV)} (got '{name}')"]
    return [f"{var} is not set" for var in REQUIRED_ENV[name] if not os.environ.get(var, "").strip()]


def model_name(name: Optional[str] = None) -> str:
    name = name or selected_provider()
    if name == "azure_openai":
        return os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
    if name == "foundry_claude":
        return os.environ.get("FOUNDRY_CLAUDE_DEPLOYMENT", "claude-opus-5-5")
    return config.MODEL if name == "anthropic" else ""


def get_provider(name: Optional[str] = None) -> Provider:
    name = name or selected_provider()
    problems = configuration_problems(name)
    if problems:
        raise ProviderConfigError("; ".join(problems))
    return _FACTORIES[name]()


__all__ = [
    "AssistantTurn", "Message", "ModelResponse", "Provider", "ProviderConfigError", "ProviderError",
    "ToolCall", "ToolResult", "ToolResults", "UserText", "configuration_problems", "get_provider",
    "model_name", "selected_provider", "REQUIRED_ENV",
]

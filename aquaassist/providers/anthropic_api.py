"""Claude via the Anthropic SDK: the Anthropic API (`anthropic`) or Microsoft Foundry (`foundry_claude`)."""
from __future__ import annotations

import json
import os
from typing import Any, List

import anthropic

from .. import config
from .base import Message, ModelResponse, ProviderError
from .convert import from_anthropic_response, to_anthropic_messages

# Server-side refusal fallback: if a safety classifier declines, the API re-runs the request on a
# recommended fallback model. Available on the Anthropic API, not on Microsoft Foundry.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _map_error(exc: Exception) -> ProviderError:
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError("rate_limit", 429)
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ProviderError("auth", exc.status_code)
    if isinstance(exc, anthropic.APIStatusError):
        return ProviderError("upstream", exc.status_code)
    return ProviderError("unavailable")


class AnthropicProvider:
    def __init__(self, name: str, client: Any, model: str, server_fallback: bool):
        self.name = name
        self.model = model
        self._client = client
        self._server_fallback = server_fallback

    def _create(self, **kwargs: Any) -> Any:
        if self._server_fallback:
            kwargs.update(betas=[FALLBACK_BETA], extra_body={"fallbacks": "default"})
        try:
            return self._client.beta.messages.create(model=self.model, **kwargs)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise _map_error(exc) from None  # `from None`: upstream text stays out of tracebacks

    def complete(self, system: str, messages: List[Message], tools: List[dict],
                 show_thinking: bool = False) -> ModelResponse:
        response = self._create(
            max_tokens=16000,
            system=system,
            tools=tools,
            thinking={"type": "adaptive", "display": "summarized" if show_thinking else "omitted"},
            output_config={"effort": config.AGENT_EFFORT},
            cache_control={"type": "ephemeral"},  # system prompt + tools are a stable prefix
            messages=to_anthropic_messages(messages),
        )
        return from_anthropic_response(response)

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        response = self._create(
            max_tokens=8000,
            output_config={"effort": config.EXTRACTION_EFFORT,
                           "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise ProviderError("upstream")
        return json.loads(next(b.text for b in response.content if b.type == "text"))


def anthropic_from_env() -> AnthropicProvider:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return AnthropicProvider("anthropic", client, config.MODEL, server_fallback=True)


def foundry_claude_from_env() -> AnthropicProvider:
    client = anthropic.AnthropicFoundry(
        base_url=os.environ.get("ANTHROPIC_FOUNDRY_BASE_URL"),  # https://<resource>.services.ai.azure.com/anthropic
        api_key=os.environ.get("ANTHROPIC_FOUNDRY_API_KEY"),
    )
    model = os.environ.get("FOUNDRY_CLAUDE_DEPLOYMENT", "claude-opus-5-5")
    return AnthropicProvider("foundry_claude", client, model, server_fallback=False)

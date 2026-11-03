"""Fake SDK clients for every provider, so tests run offline.

A test describes what the model should do with provider-neutral steps (`Reply`, `Call`,
`Refuse`, `Fail`). Each fake SDK client renders those steps as that SDK's own response
objects (or raises that SDK's own exceptions), so the real conversion code is exercised.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace as NS
from typing import Any, List, Tuple

import anthropic
import httpx
import openai

from aquaassist.providers.anthropic_api import AnthropicProvider
from aquaassist.providers.azure_openai import AzureOpenAIProvider

PROVIDERS = ["anthropic", "azure_openai", "foundry_claude"]

# Environment that makes each provider count as configured (dummy values, never real keys).
DUMMY_ENV = {
    "anthropic": {"ANTHROPIC_API_KEY": "test-placeholder"},
    "azure_openai": {"AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
                     "AZURE_OPENAI_DEPLOYMENT": "gpt-test", "AZURE_OPENAI_API_KEY": "test-placeholder"},
    "foundry_claude": {"ANTHROPIC_FOUNDRY_BASE_URL": "https://example.services.ai.azure.com/anthropic",
                       "ANTHROPIC_FOUNDRY_API_KEY": "test-placeholder"},
}


@dataclass
class Call:
    name: str
    args: dict
    id: str = "call_1"


@dataclass
class Reply:
    text: str
    calls: List[Call] = field(default_factory=list)
    thinking: str = ""
    truncated: bool = False


@dataclass
class Refuse:
    pass


@dataclass
class Fail:
    kind: str  # "rate_limit" | "auth" | "upstream" | "unavailable"
    message: str = "upstream says: bad key sk-ant-secret"


def tool_step(name: str, args: dict, call_id: str = "call_1") -> Reply:
    return Reply("", [Call(name, args, call_id)])


_REQUEST = httpx.Request("POST", "https://example.invalid/v1")


def _sdk_error(sdk: Any, step: Fail) -> Exception:
    if step.kind == "unavailable":
        return sdk.APIConnectionError(request=_REQUEST)
    status, cls = {"rate_limit": (429, sdk.RateLimitError), "auth": (401, sdk.AuthenticationError),
                   "upstream": (500, sdk.InternalServerError)}[step.kind]
    return cls(step.message, response=httpx.Response(status, request=_REQUEST), body=None)


def _anthropic_response(step: Any) -> NS:
    if isinstance(step, Refuse):
        return NS(stop_reason="refusal", content=[])
    content = [NS(type="thinking", thinking=step.thinking, signature="sig")] if step.thinking else []
    if step.text:
        content.append(NS(type="text", text=step.text))
    content += [NS(type="tool_use", id=c.id, name=c.name, input=c.args) for c in step.calls]
    stop = "tool_use" if step.calls else ("max_tokens" if step.truncated else "end_turn")
    return NS(stop_reason=stop, content=content)


def _openai_response(step: Any) -> NS:
    if isinstance(step, Refuse):
        return NS(choices=[NS(finish_reason="stop", message=NS(content=None, tool_calls=None,
                                                               refusal="I can't help with that."))])
    calls = [NS(id=c.id, type="function", function=NS(name=c.name, arguments=json.dumps(c.args)))
             for c in step.calls] or None
    finish = "tool_calls" if step.calls else ("length" if step.truncated else "stop")
    return NS(choices=[NS(finish_reason=finish, message=NS(content=step.text or None, tool_calls=calls,
                                                           refusal=None))])


class _Scripted:
    def __init__(self, steps: Tuple[Any, ...], render: Any, sdk: Any):
        self.steps, self.render, self.sdk = list(steps), render, sdk
        self.calls: List[dict] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append({**kwargs, "messages": json.loads(json.dumps(kwargs["messages"], default=vars))})
        step = self.steps[len(self.calls) - 1]
        if isinstance(step, Fail):
            raise _sdk_error(self.sdk, step)
        return self.render(step)


def scripted_provider(kind: str, *steps: Any) -> Tuple[Any, List[dict]]:
    """Returns (provider, calls): a real provider class wired to a fake SDK client."""
    if kind == "azure_openai":
        fake = _Scripted(steps, _openai_response, openai)
        client = NS(chat=NS(completions=fake))
        return AzureOpenAIProvider(client, "gpt-test"), fake.calls
    fake = _Scripted(steps, _anthropic_response, anthropic)
    client = NS(beta=NS(messages=fake))
    return AnthropicProvider(kind, client, "claude-test", server_fallback=(kind == "anthropic")), fake.calls


def conversation_turns(call: dict) -> int:
    """Messages in a request excluding the OpenAI system message, for cross-provider comparisons."""
    return sum(1 for m in call["messages"] if m.get("role") != "system")

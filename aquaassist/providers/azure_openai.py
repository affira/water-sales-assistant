"""Azure OpenAI in Microsoft Foundry via the official `openai` SDK (Chat Completions).

Recommended setup (Azure OpenAI v1 API): the plain `OpenAI()` client with
base_url=<endpoint>/openai/v1/ and no api-version. Setting AZURE_OPENAI_API_VERSION to a dated
version (e.g. 2024-10-21) switches to the older `AzureOpenAI()` client instead.
"""
from __future__ import annotations

import json
import os
from typing import Any, List

import openai

from .base import Message, ModelResponse, ProviderError
from .convert import from_openai_response, to_openai_messages, to_openai_tools


def _map_error(exc: Exception) -> ProviderError:
    if isinstance(exc, openai.RateLimitError):
        return ProviderError("rate_limit", 429)
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ProviderError("auth", exc.status_code)
    if isinstance(exc, openai.APIStatusError):
        return ProviderError("upstream", exc.status_code)
    return ProviderError("unavailable")


class AzureOpenAIProvider:
    name = "azure_openai"

    def __init__(self, client: Any, deployment: str):
        self.model = deployment  # Azure routes by deployment name, not model name
        self._client = client

    def _create(self, **kwargs: Any) -> Any:
        try:
            return self._client.chat.completions.create(model=self.model, **kwargs)
        except (openai.APIStatusError, openai.APIConnectionError) as exc:
            raise _map_error(exc) from None

    def complete(self, system: str, messages: List[Message], tools: List[dict],
                 show_thinking: bool = False) -> ModelResponse:
        # Chat Completions returns no reasoning text, so `show_thinking` has nothing to show here.
        response = self._create(
            messages=to_openai_messages(system, messages),
            tools=to_openai_tools(tools),
            max_completion_tokens=16000,
        )
        return from_openai_response(response)

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        response = self._create(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "record", "schema": schema, "strict": True}},
            max_completion_tokens=16000,
        )
        message = response.choices[0].message
        if getattr(message, "refusal", None) or not message.content:
            raise ProviderError("upstream")
        return json.loads(message.content)


def _base_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    return endpoint[: -len("/openai/v1")] if endpoint.endswith("/openai/v1") else endpoint


def azure_openai_from_env() -> AzureOpenAIProvider:
    endpoint = _base_endpoint(os.environ.get("AZURE_OPENAI_ENDPOINT", ""))
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "v1").strip() or "v1"
    if api_version == "v1":
        client: Any = openai.OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=api_key)
    else:
        client = openai.AzureOpenAI(azure_endpoint=endpoint, api_version=api_version, api_key=api_key)
    return AzureOpenAIProvider(client, os.environ.get("AZURE_OPENAI_DEPLOYMENT", ""))

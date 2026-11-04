"""Provider selection, configuration and format conversion (offline)."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import anthropic
import openai
import pytest
from fakes import DUMMY_ENV, PROVIDERS, Reply, scripted_provider

from aquaassist import config, providers
from aquaassist.agent import SYSTEM_PROMPT
from aquaassist.extraction import RECORD_SCHEMA
from aquaassist.providers import AssistantTurn, ToolCall, ToolResult, ToolResults, UserText
from aquaassist.providers.anthropic_api import FALLBACK_BETA
from aquaassist.providers.convert import from_openai_response, to_openai_messages, to_openai_tools
from aquaassist.tools import TOOLS

ALL_VARS = sorted({v for env in DUMMY_ENV.values() for v in env} | {"LLM_PROVIDER", "AZURE_OPENAI_API_VERSION"})


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for var in ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# --- selection and configuration ---------------------------------------------------

def test_default_provider_is_anthropic(clean_env: pytest.MonkeyPatch) -> None:
    assert providers.selected_provider() == "anthropic"


@pytest.mark.parametrize("kind", PROVIDERS)
def test_missing_variables_are_named_not_revealed(clean_env: pytest.MonkeyPatch, kind: str) -> None:
    clean_env.setenv("LLM_PROVIDER", kind)
    problems = providers.configuration_problems()
    assert problems == [f"{var} is not set" for var in providers.REQUIRED_ENV[kind]]
    with pytest.raises(providers.ProviderConfigError):
        providers.get_provider()


def test_unknown_provider_is_rejected(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("LLM_PROVIDER", "gemini")
    assert "LLM_PROVIDER must be one of" in providers.configuration_problems()[0]


@pytest.mark.parametrize("kind", PROVIDERS)
def test_each_provider_builds_from_env_offline(clean_env: pytest.MonkeyPatch, kind: str) -> None:
    clean_env.setenv("LLM_PROVIDER", kind)
    for var, value in DUMMY_ENV[kind].items():
        clean_env.setenv(var, value)
    provider = providers.get_provider()
    assert provider.name == kind and provider.model == providers.model_name()


def test_azure_v1_uses_openai_client_with_v1_base_url(clean_env: pytest.MonkeyPatch) -> None:
    for var, value in DUMMY_ENV["azure_openai"].items():
        clean_env.setenv(var, value)
    clean_env.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/openai/v1/")  # tolerated
    client = providers.get_provider("azure_openai")._client
    assert type(client) is openai.OpenAI
    assert str(client.base_url) == "https://example.openai.azure.com/openai/v1/"


def test_azure_dated_api_version_uses_azure_client(clean_env: pytest.MonkeyPatch) -> None:
    for var, value in DUMMY_ENV["azure_openai"].items():
        clean_env.setenv(var, value)
    clean_env.setenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    assert isinstance(providers.get_provider("azure_openai")._client, openai.AzureOpenAI)


def test_foundry_claude_uses_foundry_client(clean_env: pytest.MonkeyPatch) -> None:
    for var, value in DUMMY_ENV["foundry_claude"].items():
        clean_env.setenv(var, value)
    client = providers.get_provider("foundry_claude")._client
    assert isinstance(client, anthropic.AnthropicFoundry)
    assert str(client.base_url).startswith("https://example.services.ai.azure.com/anthropic")


def test_every_required_variable_is_documented() -> None:
    documented = (config.ROOT / ".env.example").read_text()
    for var in [v for vars_ in providers.REQUIRED_ENV.values() for v in vars_] + ["LLM_PROVIDER"]:
        assert f"\n{var}=" in documented


# --- request shapes ------------------------------------------------------------------

def test_anthropic_request_is_unchanged() -> None:
    provider, calls = scripted_provider("anthropic", Reply("hi"))
    provider.complete(SYSTEM_PROMPT, [UserText("hello")], TOOLS)
    sent = calls[0]
    assert sent["betas"] == [FALLBACK_BETA] and sent["extra_body"] == {"fallbacks": "default"}
    assert sent["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert sent["output_config"] == {"effort": config.AGENT_EFFORT}
    assert sent["cache_control"] == {"type": "ephemeral"}
    assert sent["tools"] is TOOLS and sent["max_tokens"] == 16000
    assert sent["messages"] == [{"role": "user", "content": "hello"}]


def test_foundry_claude_sends_no_server_fallback() -> None:
    provider, calls = scripted_provider("foundry_claude", Reply("hi"))
    provider.complete(SYSTEM_PROMPT, [UserText("hello")], TOOLS)
    assert "betas" not in calls[0] and "extra_body" not in calls[0]


def test_openai_tools_are_strict_only_when_all_fields_required() -> None:
    by_name = {t["function"]["name"]: t["function"] for t in to_openai_tools(TOOLS)}
    assert by_name["get_service_report"]["strict"] is True
    assert by_name["estimate_service_cost"]["strict"] is False  # has optional fields
    assert by_name["analyze_process_readings"]["strict"] is False
    assert by_name["estimate_service_cost"]["parameters"] is TOOLS[2]["input_schema"]


def test_openai_message_conversion() -> None:
    history = [
        UserText("q"),
        AssistantTurn("", [ToolCall("c1", "list_products", {}), ToolCall("c2", "get_service_report",
                                                                          {"report_id": "FSR-0001"})]),
        ToolResults([ToolResult("c1", "[]"), ToolResult("c2", "{}", is_error=True)]),
        AssistantTurn("answer"),
    ]
    out = to_openai_messages("sys", history)
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[2]["tool_calls"][1]["function"] == {"name": "get_service_report",
                                                   "arguments": json.dumps({"report_id": "FSR-0001"})}
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "[]"}
    assert out[4]["content"].startswith("ERROR: ")
    assert out[5] == {"role": "assistant", "content": "answer"}


def test_unparseable_openai_arguments_become_a_tool_error_not_a_crash() -> None:
    response = NS(choices=[NS(finish_reason="tool_calls", message=NS(
        content=None, refusal=None,
        tool_calls=[NS(id="c1", function=NS(name="list_products", arguments="{not json"))]))])
    result = from_openai_response(response)
    assert result.stop == "tool_use"
    assert result.tool_calls[0].arguments == {"_unparseable_arguments": "{not json"}


# --- structured output (report extraction) ---------------------------------------------

@pytest.mark.parametrize("kind", PROVIDERS)
def test_complete_json_on_every_provider(kind: str) -> None:
    provider, calls = scripted_provider(kind, Reply(json.dumps({"report_id": "FSR-0001"})))
    assert provider.complete_json("extract", "<report/>", RECORD_SCHEMA) == {"report_id": "FSR-0001"}
    if kind == "azure_openai":
        fmt = calls[0]["response_format"]
        assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
        assert fmt["json_schema"]["schema"] is RECORD_SCHEMA
    else:
        assert calls[0]["output_config"]["format"] == {"type": "json_schema", "schema": RECORD_SCHEMA}

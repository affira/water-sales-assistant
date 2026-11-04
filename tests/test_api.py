"""HTTP API tests with FastAPI's TestClient and fake SDK clients, on every provider (no network)."""
from __future__ import annotations

from typing import Any, List, Tuple

import httpx
import pytest
from fakes import DUMMY_ENV, PROVIDERS, Fail, Reply, conversation_turns, scripted_provider, tool_step
from fastapi.testclient import TestClient

from aquaassist import config, providers
from aquaassist.agent import Agent
from aquaassist.api import create_app


@pytest.fixture(params=PROVIDERS)
def kind(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Selects a provider and gives it dummy settings (the SDK client itself is always fake)."""
    monkeypatch.setenv("LLM_PROVIDER", request.param)
    for var, value in DUMMY_ENV[request.param].items():
        monkeypatch.setenv(var, value)
    return request.param


def api_with(kind: str, *steps: Any, raise_server_exceptions: bool = True) -> Tuple[TestClient, List[dict]]:
    provider, calls = scripted_provider(kind, *steps)
    app = create_app(agent_factory=lambda: Agent(provider=provider))
    return TestClient(app, raise_server_exceptions=raise_server_exceptions), calls


def chat(client: TestClient, message: str, session_id: str = "s1") -> httpx.Response:
    return client.post("/chat", json={"session_id": session_id, "message": message})


def test_health_reports_provider_and_index(kind: str, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(config, "RECORDS_PATH", tmp_path / "missing.json")
    client, _ = api_with(kind)
    assert client.get("/health").json() == {
        "status": "ok", "service_records_index": "missing", "llm_provider": kind,
        "llm_credentials": "configured", "model": providers.model_name(kind),
        "telemetry": "disabled", "key_vault": "disabled"}


def test_missing_settings_are_reported_by_name_only(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    secret_var = providers.REQUIRED_ENV[kind][-1]
    monkeypatch.delenv(secret_var)
    client, calls = api_with(kind, Reply("never reached"))
    assert client.get("/health").json()["llm_credentials"] == "missing"

    response = chat(client, "hello")
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "server_misconfigured"
    assert body["details"] == [{"field": "environment", "message": f"{secret_var} is not set"}]
    assert calls == []  # the LLM is not called


def test_chat_returns_reply_tools_and_verified_sources(kind: str) -> None:
    client, _ = api_with(kind, tool_step("search_product_docs", {"query": "feed water changes"}),
                         Reply("Recommend AquaTune [aquatune-optimizer p.1]. See also [made-up-doc p.9] and FSR-0001."))
    response = chat(client, "Permeate quality drops when our feed changes")

    assert response.status_code == 200
    body = response.json()
    assert body["reply"].startswith("Recommend AquaTune")
    assert body["tools_used"] == [{"name": "search_product_docs", "input": {"query": "feed water changes"},
                                   "is_error": False}]
    assert body["sources"] == [
        {"kind": "brochure", "id": "aquatune-optimizer", "page": 1, "verified": True},
        {"kind": "brochure", "id": "made-up-doc", "page": 9, "verified": False},
        {"kind": "service_report", "id": "FSR-0001", "page": None, "verified": False},
    ]


def test_failed_tool_is_marked_as_error(kind: str) -> None:
    client, _ = api_with(kind, tool_step("no_such_tool", {}), Reply("That lookup failed."))
    assert chat(client, "hi").json()["tools_used"][0]["is_error"] is True


def test_sessions_keep_history_and_stay_isolated(kind: str) -> None:
    client, calls = api_with(kind, Reply("first"), Reply("second"), Reply("other"))
    chat(client, "hello", "alice")
    chat(client, "and again", "alice")
    chat(client, "hi", "bob")
    assert [conversation_turns(c) for c in calls] == [1, 3, 1]  # alice's 2nd turn sees her 1st exchange


@pytest.mark.parametrize("failure, status, error", [
    ("unavailable", 503, "llm_unavailable"),
    ("rate_limit", 429, "rate_limited"),
    ("auth", 502, "upstream_auth_failed"),
    ("upstream", 502, "upstream_error"),
])
def test_llm_errors_map_to_http_errors(kind: str, failure: str, status: int, error: str) -> None:
    client, calls = api_with(kind, Fail(failure), Reply("recovered"))
    response = chat(client, "hello")
    assert response.status_code == status
    assert response.json()["error"] == error
    assert "sk-ant" not in response.text  # upstream text is never echoed
    # The failed turn was rolled back, so the session continues cleanly.
    assert chat(client, "hello again").status_code == 200
    assert conversation_turns(calls[1]) == 1


def test_unexpected_errors_return_the_standard_error_shape(kind: str) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug with secret detail")

    provider, _ = scripted_provider(kind)
    provider.complete = broken
    client = TestClient(create_app(agent_factory=lambda: Agent(provider=provider)), raise_server_exceptions=False)
    response = chat(client, "hello")
    assert response.status_code == 500
    assert response.json() == {"error": "internal_error", "message": "Something went wrong on the server.",
                               "details": []}


# --- request validation (provider-independent) ---------------------------------------

@pytest.fixture
def anthropic_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder")
    return api_with("anthropic")[0]


@pytest.mark.parametrize("payload, field", [
    ({"session_id": "s1"}, "message"),
    ({"session_id": "s1", "message": "   "}, "message"),
    ({"session_id": "s1", "message": "x" * 4001}, "message"),
    ({"session_id": "bad id!", "message": "hi"}, "session_id"),
    ({"session_id": "", "message": "hi"}, "session_id"),
    ({"session_id": "s1", "message": "hi", "extra": 1}, "extra"),
])
def test_invalid_requests_get_clear_errors(anthropic_client: TestClient, payload: dict, field: str) -> None:
    response = anthropic_client.post("/chat", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_request"
    assert field in [d["field"] for d in body["details"]]


def test_non_json_body_is_rejected(anthropic_client: TestClient) -> None:
    response = anthropic_client.post("/chat", content="not json", headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"

"""HTTP API tests with FastAPI's TestClient and the scripted (mocked) LLM."""
from __future__ import annotations

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient
from fakes import scripted_llm, text_reply, tool_call

from aquaassist import agent as agent_module
from aquaassist import config
from aquaassist.api import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def use_llm(monkeypatch: pytest.MonkeyPatch, *responses: object) -> list[dict]:
    create, calls = scripted_llm(*responses)
    monkeypatch.setattr(agent_module.llm, "create", create)
    return calls


def chat(client: TestClient, message: str, session_id: str = "s1") -> httpx.Response:
    return client.post("/chat", json={"session_id": session_id, "message": message})


def test_health_reports_index_status(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(config, "RECORDS_PATH", tmp_path / "missing.json")
    body = client.get("/health").json()
    assert body == {"status": "ok", "service_records_index": "missing", "model": config.MODEL}


def test_chat_returns_reply_tools_and_verified_sources(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    use_llm(monkeypatch,
            tool_call("search_product_docs", {"query": "feed water changes"}),
            text_reply("Recommend AquaTune [aquatune-optimizer p.1]. See also [made-up-doc p.9] and FSR-0001."))
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


def test_failed_tool_is_marked_as_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    use_llm(monkeypatch, tool_call("no_such_tool", {}), text_reply("That lookup failed."))
    body = chat(client, "hi").json()
    assert body["tools_used"][0]["is_error"] is True


def test_sessions_keep_history_and_stay_isolated(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = use_llm(monkeypatch, text_reply("first"), text_reply("second"), text_reply("other"))
    chat(client, "hello", "alice")
    chat(client, "and again", "alice")
    chat(client, "hi", "bob")
    assert [len(c["messages"]) for c in calls] == [1, 3, 1]  # alice's 2nd turn sees her 1st exchange


@pytest.mark.parametrize("payload, field", [
    ({"session_id": "s1"}, "message"),
    ({"session_id": "s1", "message": "   "}, "message"),
    ({"session_id": "s1", "message": "x" * 4001}, "message"),
    ({"session_id": "bad id!", "message": "hi"}, "session_id"),
    ({"session_id": "", "message": "hi"}, "session_id"),
    ({"session_id": "s1", "message": "hi", "extra": 1}, "extra"),
])
def test_invalid_requests_get_clear_errors(client: TestClient, payload: dict, field: str) -> None:
    response = client.post("/chat", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_request"
    assert field in [d["field"] for d in body["details"]]


def test_non_json_body_is_rejected(client: TestClient) -> None:
    response = client.post("/chat", content="not json", headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize("exc, status, error", [
    (anthropic.APIConnectionError(request=REQUEST), 503, "llm_unavailable"),
    (anthropic.RateLimitError("slow down", response=httpx.Response(429, request=REQUEST), body=None),
     429, "rate_limited"),
    (anthropic.AuthenticationError("bad key sk-ant-secret", response=httpx.Response(401, request=REQUEST), body=None),
     502, "upstream_auth_failed"),
    (anthropic.InternalServerError("boom", response=httpx.Response(500, request=REQUEST), body=None),
     502, "upstream_error"),
])
def test_llm_errors_map_to_http_errors(client: TestClient, monkeypatch: pytest.MonkeyPatch,
                                       exc: Exception, status: int, error: str) -> None:
    calls = use_llm(monkeypatch, exc, text_reply("recovered"))
    response = chat(client, "hello")
    assert response.status_code == status
    assert response.json()["error"] == error
    assert "sk-ant" not in response.text  # upstream text is never echoed
    # The failed turn was rolled back, so the session continues cleanly.
    assert chat(client, "hello again").status_code == 200
    assert len(calls[1]["messages"]) == 1

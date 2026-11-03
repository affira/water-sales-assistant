"""The agent loop on every provider, with fake SDK clients (no network)."""
from __future__ import annotations

import json

import pytest
from fakes import PROVIDERS, Call, Fail, Refuse, Reply, scripted_provider, tool_step

from aquaassist.agent import Agent
from aquaassist.providers import ProviderError


def tool_results_in(call: dict, kind: str) -> list[dict]:
    """The tool results the agent sent back, normalised to {id, content, is_error}."""
    messages = call["messages"]
    if kind == "azure_openai":
        return [{"id": m["tool_call_id"], "content": m["content"], "is_error": m["content"].startswith("ERROR: ")}
                for m in messages if m["role"] == "tool"]
    blocks = messages[-1]["content"]
    return [{"id": b["tool_use_id"], "content": b["content"], "is_error": b["is_error"]} for b in blocks]


@pytest.fixture(params=PROVIDERS)
def kind(request: pytest.FixtureRequest) -> str:
    return request.param


def test_tool_call_then_answer(kind: str) -> None:
    provider, calls = scripted_provider(
        kind, tool_step("search_product_docs", {"query": "feed water changes"}, "c1"),
        Reply("Recommend AquaTune [aquatune-optimizer p.1]."))
    events: list[str] = []
    answer = Agent(provider=provider).ask("Permeate quality drops when our feed changes",
                                          lambda k, d: events.append(k))

    assert answer == "Recommend AquaTune [aquatune-optimizer p.1]."
    assert events == ["tool_call", "tool_result"]
    [result] = tool_results_in(calls[1], kind)
    assert result["id"] == "c1" and not result["is_error"]
    assert any(hit["doc_id"] == "aquatune-optimizer" for hit in json.loads(result["content"]))


def test_parallel_tool_calls_are_all_answered(kind: str) -> None:
    provider, calls = scripted_provider(
        kind, Reply("", [Call("list_products", {}, "a"), Call("get_service_report", {"report_id": "FSR-0001"}, "b")]),
        Reply("done"))
    Agent(provider=provider).ask("two lookups")
    assert [r["id"] for r in tool_results_in(calls[1], kind)] == ["a", "b"]


def test_tool_error_is_reported_to_model(kind: str) -> None:
    provider, calls = scripted_provider(kind, tool_step("no_such_tool", {}), Reply("That lookup failed."))
    Agent(provider=provider).ask("hi")
    assert tool_results_in(calls[1], kind)[0]["is_error"] is True


def test_refusal_rolls_back_turn(kind: str) -> None:
    provider, _ = scripted_provider(kind, Refuse())
    agent = Agent(provider=provider)
    assert agent.ask("something") == "Sorry, I can't help with that request."
    assert agent.messages == []


def test_api_error_rolls_back_turn(kind: str) -> None:
    provider, _ = scripted_provider(kind, tool_step("list_products", {}), Fail("unavailable"))
    agent = Agent(provider=provider)
    with pytest.raises(ProviderError) as err:
        agent.ask("something")
    assert err.value.kind == "unavailable"
    assert agent.messages == []


def test_truncated_answer_is_flagged(kind: str) -> None:
    provider, _ = scripted_provider(kind, Reply("Partial answer", truncated=True))
    assert Agent(provider=provider).ask("long question").endswith("[Answer cut off: output limit reached.]")


def test_history_carries_into_next_turn(kind: str) -> None:
    provider, calls = scripted_provider(kind, Reply("first"), Reply("second"))
    agent = Agent(provider=provider)
    agent.ask("one")
    agent.ask("two")
    texts = [m.get("content") for m in calls[1]["messages"] if m.get("role") in ("user", "assistant")]
    assert texts[0] == "one" and texts[-1] == "two"


def test_thinking_summaries_are_emitted_for_claude() -> None:
    provider, _ = scripted_provider("anthropic", Reply("ok", thinking="Need brochure facts."))
    events: list[tuple] = []
    Agent(provider=provider, show_thinking=True).ask("hi", lambda k, d: events.append((k, d)))
    assert ("thinking", {"text": "Need brochure facts."}) in events

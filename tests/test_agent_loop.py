"""Exercise the agent loop with a scripted fake model (no API key needed)."""
import json
from types import SimpleNamespace as NS

from aquaassist import agent as agent_module


def fake_responses(*responses):
    calls = []

    def create(**kwargs):
        calls.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot; the agent keeps appending
        return responses[len(calls) - 1]
    return create, calls


def test_tool_call_then_answer(monkeypatch):
    first = NS(stop_reason="tool_use", content=[
        NS(type="thinking", thinking="Needs brochure facts."),
        NS(type="tool_use", id="t1", name="search_product_docs", input={"query": "feed water changes"}),
    ])
    second = NS(stop_reason="end_turn", content=[NS(type="text", text="Recommend AquaTune [aquatune-optimizer p.1].")])
    create, calls = fake_responses(first, second)
    monkeypatch.setattr(agent_module.llm, "create", create)

    events = []
    a = agent_module.Agent()
    answer = a.ask("Permeate quality drops when our feed changes", lambda k, d: events.append(k))

    assert "AquaTune" in answer
    assert [e for e in events if e != "thinking"] == ["tool_call", "tool_result"]
    tool_result = calls[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t1" and not tool_result["is_error"]
    assert any(hit["doc_id"] == "aquatune-optimizer" for hit in json.loads(tool_result["content"]))


def test_tool_error_is_reported_to_model(monkeypatch):
    first = NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="no_such_tool", input={})])
    second = NS(stop_reason="end_turn", content=[NS(type="text", text="That lookup failed.")])
    create, calls = fake_responses(first, second)
    monkeypatch.setattr(agent_module.llm, "create", create)

    agent_module.Agent().ask("hi")
    assert calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_api_error_rolls_back_turn(monkeypatch):
    def create(**kwargs):
        raise RuntimeError("network down")
    monkeypatch.setattr(agent_module.llm, "create", create)
    a = agent_module.Agent()
    try:
        a.ask("something")
    except RuntimeError:
        pass
    assert a.messages == []


def test_refusal_rolls_back_turn(monkeypatch):
    create, _ = fake_responses(NS(stop_reason="refusal", content=[]))
    monkeypatch.setattr(agent_module.llm, "create", create)
    a = agent_module.Agent()
    a.ask("something")
    assert a.messages == []

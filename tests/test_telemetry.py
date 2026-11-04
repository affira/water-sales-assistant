"""Monitoring: what is recorded, what never is, and when Azure Monitor is switched on (offline)."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import azure.monitor.opentelemetry as azure_monitor
import pytest
from fakes import (DUMMY_ENV, FAKE_INPUT_TOKENS, FAKE_OUTPUT_TOKENS, PROVIDERS, Fail, Reply,
                   scripted_provider, tool_step)
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from aquaassist import providers, telemetry
from aquaassist.agent import Agent
from aquaassist.api import create_app

PERSONAL_MESSAGE = "Hi, I'm Maria Virtanen, call me on +358 40 1234567 about our plant"
PERSONAL_QUERY = "Maria Virtanen plant permeate problem"
REPLY_TEXT = "Recommend AquaTune [aquatune-optimizer p.1]."
SESSION_ID = "maria-virtanen-session"


@pytest.fixture
def otel(monkeypatch: pytest.MonkeyPatch) -> Tuple[InMemorySpanExporter, InMemoryMetricReader]:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    monkeypatch.setattr(telemetry, "_current",
                        telemetry.Telemetry(tracer_provider, MeterProvider(metric_readers=[reader])))
    return exporter, reader


@pytest.fixture(params=PROVIDERS)
def kind(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("LLM_PROVIDER", request.param)
    for var, value in DUMMY_ENV[request.param].items():
        monkeypatch.setenv(var, value)
    return request.param


def post_chat(kind: str, *steps: Any) -> Any:
    provider, _ = scripted_provider(kind, *steps)
    client = TestClient(create_app(agent_factory=lambda: Agent(provider=provider)))
    return client.post("/chat", json={"session_id": SESSION_ID, "message": PERSONAL_MESSAGE})


def spans_by_name(exporter: InMemorySpanExporter) -> Dict[str, List[Any]]:
    out: Dict[str, List[Any]] = defaultdict(list)
    for span in exporter.get_finished_spans():
        out[span.name].append(span)
    return out


def metric_points(reader: InMemoryMetricReader) -> Dict[str, List[Tuple[dict, Any]]]:
    out: Dict[str, List[Tuple[dict, Any]]] = defaultdict(list)
    for resource in reader.get_metrics_data().resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                for point in metric.data.data_points:
                    value = getattr(point, "value", None)
                    out[metric.name].append((dict(point.attributes), value if value is not None else point.count))
    return out


def everything_exported(exporter: InMemorySpanExporter, reader: InMemoryMetricReader) -> str:
    spans = [{"name": s.name, "attributes": dict(s.attributes), "status": str(s.status.description),
              "events": [dict(e.attributes or {}) for e in s.events]} for s in exporter.get_finished_spans()]
    return json.dumps({"spans": spans, "metrics": metric_points(reader)}, default=str)


def test_chat_records_latency_tools_and_tokens(otel, kind: str) -> None:
    exporter, reader = otel
    assert post_chat(kind, tool_step("search_product_docs", {"query": PERSONAL_QUERY}),
                     Reply(REPLY_TEXT)).status_code == 200

    spans = spans_by_name(exporter)
    [turn] = spans["chat.turn"]
    assert turn.attributes["aquaassist.tools"] == ("search_product_docs",)
    assert turn.attributes["aquaassist.tool_count"] == 1
    assert turn.attributes["http.response.status_code"] == 200
    assert len(spans["llm.complete"]) == 2
    for llm in spans["llm.complete"]:
        assert llm.attributes["gen_ai.system"] == kind
        assert llm.attributes["gen_ai.usage.input_tokens"] == FAKE_INPUT_TOKENS
        assert llm.attributes["gen_ai.usage.output_tokens"] == FAKE_OUTPUT_TOKENS
        assert llm.parent.span_id == turn.context.span_id  # LLM calls nest under their request

    points = metric_points(reader)
    assert [(a["outcome"], n) for a, n in points["aquaassist.chat.duration"]] == [("ok", 1)]
    assert [(a["tool"], n) for a, n in points["aquaassist.tool.calls"]] == [("search_product_docs", 1)]
    tokens = {a["gen_ai.token.type"]: n for a, n in points["aquaassist.llm.tokens"]}
    assert tokens == {"input": 2 * FAKE_INPUT_TOKENS, "output": 2 * FAKE_OUTPUT_TOKENS}
    assert sum(n for _, n in points["aquaassist.llm.duration"]) == 2


def test_no_message_text_or_personal_data_is_recorded(otel, kind: str) -> None:
    exporter, reader = otel
    post_chat(kind, tool_step("search_product_docs", {"query": PERSONAL_QUERY}), Reply(REPLY_TEXT))
    exported = everything_exported(exporter, reader)
    for sensitive in ("Maria", "Virtanen", "+358", "1234567", "AquaTune", SESSION_ID, "test-placeholder"):
        assert sensitive not in exported


def test_llm_errors_are_recorded_by_code(otel, kind: str) -> None:
    exporter, reader = otel
    assert post_chat(kind, Fail("rate_limit")).status_code == 429

    spans = spans_by_name(exporter)
    [turn] = spans["chat.turn"]
    assert turn.status.status_code == StatusCode.ERROR
    assert turn.attributes["aquaassist.error"] == "rate_limited"
    [llm] = spans["llm.complete"]
    assert llm.status.status_code == StatusCode.ERROR and llm.status.description == "rate_limit"
    points = metric_points(reader)
    assert [(a["error"], n) for a, n in points["aquaassist.chat.errors"]] == [("rate_limited", 1)]
    assert [a["outcome"] for a, _ in points["aquaassist.llm.duration"]] == ["rate_limit"]
    assert "sk-ant" not in everything_exported(exporter, reader)  # upstream error text never exported


def test_unexpected_errors_record_a_code_but_no_exception_text(otel, kind: str) -> None:
    exporter, reader = otel
    provider, _ = scripted_provider(kind)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"crash while handling: {PERSONAL_MESSAGE}")

    provider.complete = broken
    client = TestClient(create_app(agent_factory=lambda: Agent(provider=provider)), raise_server_exceptions=False)
    assert client.post("/chat", json={"session_id": SESSION_ID, "message": PERSONAL_MESSAGE}).status_code == 500

    [turn] = spans_by_name(exporter)["chat.turn"]
    assert turn.status.status_code == StatusCode.ERROR and turn.status.description == "internal_error"
    assert all(not s.events for s in exporter.get_finished_spans())  # no recorded exceptions
    assert "Maria" not in everything_exported(exporter, reader)


def test_misconfiguration_is_recorded(otel, kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    exporter, reader = otel
    monkeypatch.delenv(providers.REQUIRED_ENV[kind][-1])
    assert post_chat(kind, Reply("never reached")).status_code == 500
    assert [(a["error"], n) for a, n in metric_points(reader)["aquaassist.chat.errors"]] == [
        ("server_misconfigured", 1)]
    assert "llm.complete" not in spans_by_name(exporter)


# --- switching Azure Monitor on ---------------------------------------------------------

@pytest.fixture
def fake_configure(monkeypatch: pytest.MonkeyPatch) -> List[dict]:
    calls: List[dict] = []
    monkeypatch.setattr(azure_monitor, "configure_azure_monitor", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(telemetry, "_enabled", False)
    monkeypatch.setattr(telemetry, "_current", None)
    exported_logger = logging.getLogger(telemetry.EXPORTED_LOGGER)
    monkeypatch.setattr(exported_logger, "level", exported_logger.level)  # restore after the test
    return calls


def test_monitoring_stays_off_without_connection_string(fake_configure: List[dict]) -> None:
    assert telemetry.setup_telemetry() is False
    assert fake_configure == [] and not telemetry.enabled()


def test_monitoring_turns_on_with_connection_string(fake_configure: List[dict], monkeypatch: pytest.MonkeyPatch,
                                                    caplog: pytest.LogCaptureFixture) -> None:
    secret = "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://x"
    monkeypatch.setenv(telemetry.CONNECTION_STRING_VAR, secret)
    with caplog.at_level(logging.DEBUG):
        assert telemetry.setup_telemetry() is True
        assert telemetry.setup_telemetry() is True  # idempotent
    assert fake_configure == [{"connection_string": secret, "logger_name": "aquaassist"}]
    assert telemetry.enabled()
    assert "InstrumentationKey" not in caplog.text


def test_health_reports_monitoring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder")
    monkeypatch.setattr(telemetry, "_enabled", True)
    provider, _ = scripted_provider("anthropic")
    client = TestClient(create_app(agent_factory=lambda: Agent(provider=provider)))
    assert client.get("/health").json()["telemetry"] == "enabled"

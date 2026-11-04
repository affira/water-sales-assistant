"""Monitoring with OpenTelemetry, exported to Azure Monitor (Application Insights) when configured.

The code below always talks to the OpenTelemetry API, which does nothing until a provider is
set up. `setup_telemetry()` sets one up only when APPLICATIONINSIGHTS_CONNECTION_STRING is set.

What is recorded:  request latency and outcome, tool *names* per request, tool-call counts,
                   LLM latency, input/output token counts, error codes.
What never is:     message or reply text, tool inputs (they can echo user text), session ids,
                   connection strings or keys.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger("aquaassist.telemetry")

CONNECTION_STRING_VAR = "APPLICATIONINSIGHTS_CONNECTION_STRING"
EXPORTED_LOGGER = "aquaassist"  # only our own logs (error types, never content) go to Azure Monitor


class Telemetry:
    """Spans and metric instruments for the assistant. Safe to use when monitoring is off."""

    def __init__(self, tracer_provider: Any = None, meter_provider: Any = None):
        self.tracer = trace.get_tracer("aquaassist", tracer_provider=tracer_provider)
        meter = metrics.get_meter("aquaassist", meter_provider=meter_provider)
        self.chat_duration = meter.create_histogram(
            "aquaassist.chat.duration", unit="ms", description="Time to answer one /chat request")
        self.chat_errors = meter.create_counter(
            "aquaassist.chat.errors", description="Failed /chat requests by error code")
        self.tool_calls = meter.create_counter(
            "aquaassist.tool.calls", description="Tool calls by tool name and outcome")
        self.llm_duration = meter.create_histogram(
            "aquaassist.llm.duration", unit="ms", description="Latency of one LLM call")
        self.llm_tokens = meter.create_counter(
            "aquaassist.llm.tokens", unit="{token}", description="LLM tokens by direction")

    def _span(self, name: str, attributes: Dict[str, Any]) -> Any:
        # Exceptions are NOT auto-recorded: their messages and stack traces could contain user data.
        # Callers set an error status with a code instead.
        return self.tracer.start_as_current_span(name, attributes=attributes, record_exception=False,
                                                 set_status_on_exception=False)

    @contextmanager
    def chat_turn(self, provider: str) -> Iterator[Dict[str, Any]]:
        """Wraps one /chat request. The caller fills `status`, `tools` [(name, is_error)] and `error`."""
        record: Dict[str, Any] = {"status": 200, "tools": [], "error": None}
        start = time.perf_counter()
        with self._span("chat.turn", {"llm.provider": provider}) as span:
            try:
                yield record
            except Exception:
                record.update(status=500, error="internal_error")
                raise
            finally:
                self._finish_chat(span, provider, record, (time.perf_counter() - start) * 1000)

    def _finish_chat(self, span: Any, provider: str, record: Dict[str, Any], ms: float) -> None:
        tools: List[Tuple[str, bool]] = record["tools"]
        error: Optional[str] = record["error"]
        span.set_attribute("aquaassist.tools", [name for name, _ in tools])
        span.set_attribute("aquaassist.tool_count", len(tools))
        span.set_attribute("http.response.status_code", record["status"])
        if error:
            span.set_attribute("aquaassist.error", error)
            span.set_status(Status(StatusCode.ERROR, error))
            self.chat_errors.add(1, {"llm.provider": provider, "error": error})
        outcome = error or "ok"
        self.chat_duration.record(ms, {"llm.provider": provider, "outcome": outcome,
                                       "http.response.status_code": record["status"]})
        for name, is_error in tools:
            self.tool_calls.add(1, {"tool": name, "is_error": is_error})

    @contextmanager
    def llm_call(self, provider: str, model: str) -> Iterator[Dict[str, Any]]:
        """Wraps one LLM call. The caller fills `input_tokens` / `output_tokens` when known."""
        attrs = {"gen_ai.system": provider, "gen_ai.request.model": model}
        usage: Dict[str, Any] = {}
        start = time.perf_counter()
        with self._span("llm.complete", attrs) as span:
            outcome = "ok"
            try:
                yield usage
            except Exception as exc:
                outcome = getattr(exc, "kind", "error")  # ProviderError.kind, never the message
                span.set_status(Status(StatusCode.ERROR, outcome))
                raise
            finally:
                self.llm_duration.record((time.perf_counter() - start) * 1000, {**attrs, "outcome": outcome})
                self._record_tokens(span, attrs, usage)

    def _record_tokens(self, span: Any, attrs: Dict[str, str], usage: Dict[str, Any]) -> None:
        for direction in ("input", "output"):
            count = usage.get(f"{direction}_tokens")
            if isinstance(count, int):
                span.set_attribute(f"gen_ai.usage.{direction}_tokens", count)
                self.llm_tokens.add(count, {**attrs, "gen_ai.token.type": direction})


_current: Optional[Telemetry] = None
_enabled = False


def current() -> Telemetry:
    global _current
    if _current is None:
        _current = Telemetry()
    return _current


def enabled() -> bool:
    return _enabled


def setup_telemetry() -> bool:
    """Turn on export to Azure Monitor if a connection string is configured. Idempotent."""
    global _enabled, _current
    if _enabled:
        return True
    connection_string = os.environ.get(CONNECTION_STRING_VAR, "").strip()
    if not connection_string:
        return False
    from azure.monitor.opentelemetry import configure_azure_monitor  # imported only when used

    # Also auto-instruments FastAPI (request spans with method, route, status and duration;
    # no bodies or headers). Must run before the FastAPI app is created.
    configure_azure_monitor(connection_string=connection_string, logger_name=EXPORTED_LOGGER)
    logging.getLogger(EXPORTED_LOGGER).setLevel(logging.INFO)
    _current, _enabled = None, True  # rebuild instruments against the configured providers
    logger.info("Azure Monitor telemetry enabled")
    return True

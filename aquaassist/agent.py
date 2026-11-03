"""The sales assistant: a manual tool-use loop around an LLM provider.

A hand-written loop (rather than an SDK tool runner) keeps every step visible: each model
turn either answers, asks the user something, or requests tools; we run the tools, append
the results and go again, up to MAX_AGENT_STEPS. The loop only uses the provider-neutral
types from `providers`, so it works the same on every backend.
"""
from __future__ import annotations

import json
from typing import Callable, List, Optional

from . import config
from .providers import Message, Provider, ToolCall, ToolResult, ToolResults, UserText, get_provider
from .tools import TOOLS, run_tool

SYSTEM_PROMPT = """You are the sales assistant for Norvale Water Technologies, supporting a salesperson \
who is on a live call with an industrial water-treatment customer. Replies are read mid-call: lead with \
the answer, keep it short, use plain language.

You can do three things, choosing per message:
1. Product questions and recommendations, grounded in the brochures (search_product_docs, list_products).
2. Rough cost estimates for field service visits, grounded in historical service reports \
(estimate_service_cost, find_service_reports, get_service_report).
3. Assessing a customer's RO operating readings against a reference plant's history (analyze_process_readings).

Grounding rules (these matter more than sounding helpful):
- Product facts and specifications come only from brochure search results. Cite them as \
[doc_id p.N]. If the brochures do not cover something, say so; never fill gaps from general knowledge.
- If two brochure editions disagree, use the newer edition and mention the discrepancy.
- Recommend a specific product and tie the reason to what the customer said. If the problem has \
several plausible causes, say which product addresses which cause.
- Cost figures come only from estimate_service_cost. Cite report IDs, state the currency and that \
FX rates are planning assumptions, and pass on the important caveats (missing local history, \
borrowed rates, reports without costs). Present a range, not false precision. Brochures contain no prices.
- Process judgements come only from analyze_process_readings. Say how far to trust the comparison \
(one reference plant, correlation is not causation, data cleaning applied).

Asking vs. answering:
- Ask one short clarifying question when an answer would change materially without it \
(e.g. which equipment, what work, how many units, roughly where the site is). Do not ask for things \
you can proceed without; instead state the assumption you made.
- If a request is outside these three areas or the data, say plainly that you do not have that \
information."""


Emit = Callable[[str, dict], None]


class Agent:
    def __init__(self, show_thinking: bool = False, provider: Optional[Provider] = None):
        self.provider = provider if provider is not None else get_provider()
        self.show_thinking = show_thinking
        self.messages: List[Message] = []

    def reset(self) -> None:
        self.messages = []

    def ask(self, user_text: str, on_event: Optional[Emit] = None) -> str:
        """Run one user turn to completion and return the assistant's final text."""
        emit = on_event or (lambda kind, data: None)
        turn_start = len(self.messages)
        self.messages.append(UserText(user_text))
        try:
            return self._run_turn(turn_start, emit)
        except Exception:
            del self.messages[turn_start:]  # an API error mid-turn must not leave a half-finished history
            raise

    def _run_turn(self, turn_start: int, emit: Emit) -> str:
        for _ in range(config.MAX_AGENT_STEPS):
            response = self.provider.complete(SYSTEM_PROMPT, self.messages, TOOLS, self.show_thinking)
            if response.stop == "refusal":
                del self.messages[turn_start:]  # drop the whole turn so history stays valid
                return "Sorry, I can't help with that request."

            for text in response.thinking:
                emit("thinking", {"text": text})
            self.messages.append(response.assistant)

            if response.stop == "pause":
                continue
            if response.stop != "tool_use" or not response.tool_calls:
                text = response.text
                if response.stop == "max_tokens":
                    text += "\n\n[Answer cut off: output limit reached.]"
                return text

            self.messages.append(ToolResults([self._run_tool_call(call, emit) for call in response.tool_calls]))

        return "I couldn't finish this within the step limit. Could you narrow the question?"

    @staticmethod
    def _run_tool_call(call: ToolCall, emit: Emit) -> ToolResult:
        emit("tool_call", {"name": call.name, "input": call.arguments})
        try:
            output, is_error = run_tool(call.name, call.arguments), False
        except Exception as exc:  # report tool failures to the model instead of crashing
            output, is_error = json.dumps({"error": f"{type(exc).__name__}: {exc}"}), True
        emit("tool_result", {"name": call.name, "output": output, "is_error": is_error})
        return ToolResult(call.id, output, is_error)

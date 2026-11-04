"""The one place where the internal format is translated to and from each provider's wire format.

Anthropic: tool calls are `tool_use` content blocks; results are `tool_result` blocks in a user turn.
OpenAI:    tool calls are `message.tool_calls` with JSON-string arguments; each result is a
           separate `role: "tool"` message.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .base import AssistantTurn, Message, ModelResponse, StopReason, ToolCall, ToolResults, UserText

# --- Anthropic ----------------------------------------------------------------------

_ANTHROPIC_STOP: Dict[str, StopReason] = {
    "end_turn": "end", "stop_sequence": "end", "tool_use": "tool_use",
    "max_tokens": "max_tokens", "refusal": "refusal", "pause_turn": "pause",
}


def anthropic_replayable(content: List[Any]) -> List[Any]:
    """Blocks safe to echo back next turn: after a mid-output refusal fallback, the declining
    model's thinking and tool calls before the last `fallback` marker must be dropped."""
    last = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    if last < 0:
        return list(content)
    drop = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [b for i, b in enumerate(content) if i > last or b.type not in drop]


def _anthropic_assistant_blocks(turn: AssistantTurn) -> List[Any]:
    if turn.opaque is not None:
        return turn.opaque
    blocks: List[Any] = [{"type": "text", "text": turn.text}] if turn.text else []
    blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in turn.tool_calls]
    return blocks


def to_anthropic_messages(messages: List[Message]) -> List[dict]:
    out = []
    for m in messages:
        if isinstance(m, UserText):
            out.append({"role": "user", "content": m.text})
        elif isinstance(m, AssistantTurn):
            out.append({"role": "assistant", "content": _anthropic_assistant_blocks(m)})
        elif isinstance(m, ToolResults):
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
                for r in m.results]})
    return out


def _anthropic_usage(response: Any) -> Tuple[Optional[int], Optional[int]]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None
    # Cached prompt tokens are reported separately from input_tokens; count all of them as input.
    fields = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    parts = (getattr(usage, f, None) for f in fields)
    return sum(p for p in parts if isinstance(p, int)), getattr(usage, "output_tokens", None)


def from_anthropic_response(response: Any) -> ModelResponse:
    content = anthropic_replayable(response.content)
    text = "\n".join(b.text for b in content if b.type == "text").strip()
    calls = [ToolCall(b.id, b.name, dict(b.input)) for b in content if b.type == "tool_use"]
    thinking = [b.thinking for b in content if b.type == "thinking" and getattr(b, "thinking", "")]
    stop = _ANTHROPIC_STOP.get(response.stop_reason, "end")
    return ModelResponse(AssistantTurn(text, calls, opaque=content), stop, thinking, *_anthropic_usage(response))


# --- OpenAI (Chat Completions) ---------------------------------------------------------

def to_openai_tools(tools: List[dict]) -> List[dict]:
    """OpenAI strict mode needs every property required, so `strict` is kept only where that holds."""
    out = []
    for t in tools:
        schema = t["input_schema"]
        all_required = set(schema.get("required", [])) == set(schema.get("properties", {}))
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": schema,
            "strict": bool(t.get("strict")) and all_required}})
    return out


def to_openai_messages(system: str, messages: List[Message]) -> List[dict]:
    out: List[dict] = [{"role": "system", "content": system}]
    for m in messages:
        if isinstance(m, UserText):
            out.append({"role": "user", "content": m.text})
        elif isinstance(m, AssistantTurn):
            msg: dict = {"role": "assistant", "content": m.text or None}
            if m.tool_calls:
                msg["tool_calls"] = [{"id": c.id, "type": "function",
                                      "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                                     for c in m.tool_calls]
            out.append(msg)
        elif isinstance(m, ToolResults):
            # OpenAI has no is_error flag, so failures are marked in the content.
            out += [{"role": "tool", "tool_call_id": r.call_id,
                     "content": f"ERROR: {r.content}" if r.is_error else r.content} for r in m.results]
    return out


def _parse_arguments(raw: str) -> Dict[str, Any]:
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        args = None
    # Unparseable arguments make the tool call fail, and the error goes back to the model.
    return args if isinstance(args, dict) else {"_unparseable_arguments": raw}


def _openai_usage(response: Any) -> Tuple[Optional[int], Optional[int]]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None
    return getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)


def from_openai_response(response: Any) -> ModelResponse:
    choice = response.choices[0]
    message = choice.message
    tokens = _openai_usage(response)
    if getattr(message, "refusal", None) or choice.finish_reason == "content_filter":
        return ModelResponse(AssistantTurn(""), "refusal", [], *tokens)
    calls = [ToolCall(c.id, c.function.name, _parse_arguments(c.function.arguments))
             for c in (message.tool_calls or [])]
    if calls:
        stop: StopReason = "tool_use"
    elif choice.finish_reason == "length":
        stop = "max_tokens"
    else:
        stop = "end"
    return ModelResponse(AssistantTurn((message.content or "").strip(), calls), stop, [], *tokens)

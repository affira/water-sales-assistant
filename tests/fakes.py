"""A scripted stand-in for `aquaassist.llm.create`, so tests never touch the network."""
from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any, Callable


def scripted_llm(*responses: Any) -> tuple[Callable[..., Any], list[dict]]:
    """Returns (create, calls). Each call returns the next response, or raises it if it is an exception."""
    calls: list[dict] = []

    def create(**kwargs: Any) -> Any:
        calls.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot; the agent keeps appending
        response = responses[len(calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response
    return create, calls


def text_reply(text: str) -> NS:
    return NS(stop_reason="end_turn", content=[NS(type="text", text=text)])


def tool_call(name: str, args: dict, call_id: str = "t1") -> NS:
    return NS(stop_reason="tool_use", content=[NS(type="tool_use", id=call_id, name=name, input=args)])

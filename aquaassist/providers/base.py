"""The provider-neutral message format and the interface every LLM backend implements.

The agent only ever sees these types. Translation to and from each SDK's wire format
lives in `convert.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Protocol, Union

StopReason = Literal["end", "tool_use", "max_tokens", "refusal", "pause"]
ErrorKind = Literal["auth", "rate_limit", "unavailable", "upstream"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class UserText:
    text: str


@dataclass
class AssistantTurn:
    text: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    # Provider-native content to replay verbatim (e.g. Claude's thinking blocks must be sent back
    # unchanged). Only the provider that produced it reads it; others rebuild from text/tool_calls.
    opaque: Any = None


@dataclass
class ToolResults:
    results: List[ToolResult]


Message = Union[UserText, AssistantTurn, ToolResults]


@dataclass
class ModelResponse:
    assistant: AssistantTurn
    stop: StopReason
    thinking: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.assistant.text

    @property
    def tool_calls(self) -> List[ToolCall]:
        return self.assistant.tool_calls


class ProviderError(Exception):
    """An LLM call failed. `kind` drives the HTTP status; the message never contains upstream text."""

    def __init__(self, kind: ErrorKind, status: Optional[int] = None):
        super().__init__(f"LLM provider error: {kind}" + (f" ({status})" if status else ""))
        self.kind = kind
        self.status = status


class ProviderConfigError(Exception):
    """The selected provider is unknown or its environment variables are missing."""


class Provider(Protocol):
    name: str
    model: str

    def complete(self, system: str, messages: List[Message], tools: List[dict],
                 show_thinking: bool = False) -> ModelResponse:
        """One model turn: may return text, tool calls, or both."""

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        """One structured-output call constrained to `schema` (used by report extraction)."""

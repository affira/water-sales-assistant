"""HTTP API around the agent (FastAPI).

    uvicorn aquaassist.api:app --host 0.0.0.0 --port 8000

POST /chat   one conversational turn for a session
GET  /health liveness/readiness for the container platform (never calls the LLM)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Union

import anthropic
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import config
from .agent import Agent
from .citations import extract_sources
from .sessions import SessionStore

logger = logging.getLogger("aquaassist.api")


# --- request / response models ---------------------------------------------

class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
                            description="Client-chosen conversation id (letters, digits, - and _).")
    message: str = Field(min_length=1, max_length=4000, description="The salesperson's message.")


class SourceOut(BaseModel):
    kind: Literal["brochure", "service_report"]
    id: str
    page: Optional[int] = None
    verified: bool = Field(description="True if a tool returned this source during the turn.")


class ToolUseOut(BaseModel):
    name: str
    input: Dict[str, Any]
    is_error: bool


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    sources: List[SourceOut]
    tools_used: List[ToolUseOut]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service_records_index: Literal["present", "missing"]
    model: str


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: List[Dict[str, Any]] = []


# --- turn recording ------------------------------------------------------------

@dataclass
class TurnRecorder:
    """Collects tool calls and outputs from the agent's events during one turn."""
    tools: List[ToolUseOut] = field(default_factory=list)
    outputs: List[tuple] = field(default_factory=list)
    _pending: List[int] = field(default_factory=list)

    def __call__(self, kind: str, data: dict) -> None:
        if kind == "tool_call":
            self.tools.append(ToolUseOut(name=data["name"], input=dict(data["input"]), is_error=False))
            self._pending.append(len(self.tools) - 1)
        elif kind == "tool_result":
            index = next(i for i in self._pending if self.tools[i].name == data["name"])
            self._pending.remove(index)
            self.tools[index].is_error = data["is_error"]
            self.outputs.append((data["name"], data["output"]))


# --- errors ----------------------------------------------------------------------

def error_response(status: int, error: str, message: str, details: Optional[list] = None) -> JSONResponse:
    body = ErrorResponse(error=error, message=message, details=details or [])
    return JSONResponse(status_code=status, content=body.model_dump())


def upstream_error_response(exc: Exception) -> JSONResponse:
    """Map LLM SDK errors to HTTP errors without exposing upstream messages or credentials."""
    if isinstance(exc, anthropic.RateLimitError):
        return error_response(429, "rate_limited", "The language model is rate-limited; retry shortly.")
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return error_response(502, "upstream_auth_failed",
                              "The language model rejected the server's credentials; check its configuration.")
    if isinstance(exc, anthropic.APIStatusError):
        return error_response(502, "upstream_error", f"The language model returned an error ({exc.status_code}).")
    return error_response(503, "llm_unavailable", "The language model could not be reached; retry shortly.")


def _validation_details(exc: RequestValidationError) -> list:
    return [{"field": ".".join(str(part) for part in err["loc"] if part != "body") or "body",
             "message": err["msg"]} for err in exc.errors()]


# --- app ---------------------------------------------------------------------------

def create_app(agent_factory: Callable[[], Agent] = Agent, max_sessions: int = 1000) -> FastAPI:
    app = FastAPI(title="Norvale Sales Assistant API",
                  description="Sales assistant for a fictional water-treatment company (synthetic data).")
    store = SessionStore(agent_factory, max_sessions=max_sessions)
    app.state.sessions = store

    @app.exception_handler(RequestValidationError)
    def on_invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, "invalid_request", "The request body is invalid.", _validation_details(exc))

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        index = "present" if config.RECORDS_PATH.exists() else "missing"
        return HealthResponse(status="ok", service_records_index=index, model=config.MODEL)

    @app.post("/chat", response_model=ChatResponse,
              responses={422: {"model": ErrorResponse}, 429: {"model": ErrorResponse},
                         502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
    def chat(request: ChatRequest) -> Union[ChatResponse, JSONResponse]:
        recorder = TurnRecorder()
        try:
            with store.session(request.session_id) as agent:
                reply = agent.ask(request.message, recorder)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            logger.warning("LLM call failed: %s", type(exc).__name__)  # type only: no upstream text or secrets
            return upstream_error_response(exc)
        sources = [SourceOut(**vars(s)) for s in extract_sources(reply, recorder.outputs)]
        return ChatResponse(session_id=request.session_id, reply=reply, sources=sources,
                            tools_used=recorder.tools)

    return app


app = create_app()

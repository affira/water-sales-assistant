"""Per-session conversation state for the HTTP API.

NOTE: state lives in this process's memory. That is fine for a single container, but
production would need a shared store (e.g. Redis or Azure Cosmos DB) holding each session's
message history: in-memory sessions are lost on restart or scale-to-zero, and with more than
one replica a user's next request may reach a container that has never seen their session.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .agent import Agent


@dataclass
class _Session:
    agent: Agent
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    """Keeps one Agent per session id, evicting the least recently used beyond `max_sessions`."""

    def __init__(self, agent_factory: Callable[[], Agent], max_sessions: int = 1000):
        self._factory = agent_factory
        self._max = max_sessions
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._sessions

    def _get_or_create(self, session_id: str) -> _Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = self._sessions[session_id] = _Session(self._factory())
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > self._max:
                self._sessions.popitem(last=False)
            return session

    @contextmanager
    def session(self, session_id: str) -> Iterator[Agent]:
        """Exclusive access to a session's agent, so concurrent requests can't interleave its history."""
        session = self._get_or_create(session_id)
        with session.lock:
            yield session.agent

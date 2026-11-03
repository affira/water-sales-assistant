import threading

from aquaassist.agent import Agent
from aquaassist.sessions import SessionStore


def test_same_session_returns_same_agent() -> None:
    store = SessionStore(Agent)
    with store.session("a") as first:
        pass
    with store.session("a") as second:
        pass
    assert first is second and len(store) == 1


def test_least_recently_used_session_is_evicted() -> None:
    store = SessionStore(Agent, max_sessions=2)
    for sid in ("a", "b", "a", "c"):  # "b" is now the least recently used
        with store.session(sid):
            pass
    assert "a" in store and "c" in store and "b" not in store


def test_session_access_is_exclusive() -> None:
    store = SessionStore(Agent)
    entered = threading.Event()
    with store.session("a"):
        worker = threading.Thread(target=lambda: store.session("a").__enter__() and entered.set())
        worker.start()
        worker.join(timeout=0.2)
        assert not entered.is_set()  # second request waits while the first holds the session
    worker.join(timeout=1)

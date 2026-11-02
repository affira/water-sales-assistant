"""Thin wrapper around the Anthropic SDK so every call shares the same settings."""
from __future__ import annotations

import anthropic

from . import config

# Server-side refusal fallback: if a safety classifier declines, the API re-runs the
# request on a recommended fallback model instead of returning the refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # Reads ANTHROPIC_API_KEY (or an `ant auth login` profile) from the environment.
        _client = anthropic.Anthropic()
    return _client


def create(**kwargs):
    """messages.create with the project's model, fallback beta and any per-call options."""
    return client().beta.messages.create(
        model=config.MODEL,
        betas=[FALLBACK_BETA],
        extra_body={"fallbacks": "default"},
        **kwargs,
    )


def replayable_content(content: list) -> list:
    """Content blocks that are safe to echo back in the next request.

    After a mid-output fallback, blocks produced *before* the last `fallback` marker by the
    declining model (thinking, tool calls) must not be replayed; text is kept.
    """
    last_fallback = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    if last_fallback < 0:
        return list(content)
    drop = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [b for i, b in enumerate(content) if i > last_fallback or b.type not in drop]

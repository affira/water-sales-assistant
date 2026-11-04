"""Command-line chat.

    python -m aquaassist.cli               interactive chat
    python -m aquaassist.cli --trace       also show tool calls and reasoning summaries
    python -m aquaassist.cli --once "..."  answer one message and exit
"""
from __future__ import annotations

import argparse
import json
import sys

from . import config, runtime
from .agent import Agent
from .providers import ProviderConfigError, ProviderError

DIM, CYAN, RESET = "\033[2m", "\033[36m", "\033[0m"


def make_tracer(enabled: bool):
    def on_event(kind: str, data: dict) -> None:
        if not enabled:
            return
        if kind == "thinking":
            print(f"{DIM}  · {data['text'].strip()[:400]}{RESET}")
        elif kind == "tool_call":
            print(f"{CYAN}  → {data['name']}({json.dumps(data['input'])}){RESET}")
        elif kind == "tool_result":
            preview = data["output"].replace("\n", " ")
            print(f"{DIM}  ← {'ERROR ' if data['is_error'] else ''}{preview[:300]}{'…' if len(preview) > 300 else ''}{RESET}")
    return on_event


def main() -> None:
    parser = argparse.ArgumentParser(description="Norvale sales assistant")
    parser.add_argument("--trace", action="store_true", help="show tool calls and reasoning summaries")
    parser.add_argument("--once", metavar="MESSAGE", help="answer a single message and exit")
    args = parser.parse_args()
    runtime.bootstrap()  # API keys from Key Vault or the environment; monitoring if configured

    if not config.RECORDS_PATH.exists():
        sys.exit("Service records not built yet. Run: python -m aquaassist.ingest")

    try:
        agent = Agent(show_thinking=args.trace)
    except ProviderConfigError as exc:
        sys.exit(f"LLM provider not configured: {exc}. See .env.example.")
    tracer = make_tracer(args.trace)

    def answer(text: str) -> None:
        try:
            print(f"\nAssistant: {agent.ask(text, tracer)}\n")
        except ProviderError as exc:
            if exc.kind == "auth":
                sys.exit(f"Authentication failed for provider '{agent.provider.name}': check its API key.")
            if exc.kind == "unavailable":
                print("Network error reaching the model; try again.")
            else:
                print(f"Model error ({exc.kind}{f', {exc.status}' if exc.status else ''}); try again.")

    if args.once:
        answer(args.once)
        return

    print(f"Norvale sales assistant ({agent.provider.name}: {agent.provider.model}). Commands: /reset, /quit\n")
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        if text == "/reset":
            agent.reset()
            print("(conversation cleared)\n")
            continue
        answer(text)


if __name__ == "__main__":
    main()

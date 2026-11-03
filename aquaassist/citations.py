"""Find the sources an answer cites and check them against what the tools actually returned.

Deterministic on purpose: a citation is only `verified` when a tool produced that exact
brochure page or service report during the same turn, so a made-up reference is visible.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Optional

_BRACKET = re.compile(r"\[([^\[\]]+)\]")
_DOC_PAGE = re.compile(r"\b([a-z0-9][a-z0-9-]*[a-z0-9])\s+pp?\.?\s*(\d+)(?:\s*[–—-]\s*(\d+))?\b")
_MAX_RANGE = 20  # brochures are short; a bigger "range" is more likely a typo than a citation
_REPORT_ID = re.compile(r"\bFSR-\d{4}\b")


@dataclass(frozen=True)
class Source:
    kind: str  # "brochure" or "service_report"
    id: str
    page: Optional[int]
    verified: bool


def _pages(first: str, last: str) -> range:
    start = int(first)
    end = int(last) if last and start <= int(last) <= start + _MAX_RANGE else start
    return range(start, end + 1)


def cited_brochure_pages(text: str) -> list[tuple[str, int]]:
    """`[doc_id p.N]` (or `p.N–M`) citations in order of first appearance, without duplicates."""
    found: list[tuple[str, int]] = []
    for bracket in _BRACKET.findall(text):
        for doc_id, first, last in _DOC_PAGE.findall(bracket):
            for page in _pages(first, last):
                if (doc_id, page) not in found:
                    found.append((doc_id, page))
    return found


def cited_report_ids(text: str) -> list[str]:
    return list(dict.fromkeys(_REPORT_ID.findall(text)))


def retrieved_brochure_pages(tool_outputs: Iterable[tuple[str, str]]) -> set[tuple[str, int]]:
    pages: set[tuple[str, int]] = set()
    for name, output in tool_outputs:
        if name != "search_product_docs":
            continue
        try:
            hits = json.loads(output)
        except json.JSONDecodeError:
            continue
        if isinstance(hits, list):
            pages.update((h["doc_id"], int(h["page"])) for h in hits if "doc_id" in h and "page" in h)
    return pages


def retrieved_report_ids(tool_outputs: Iterable[tuple[str, str]]) -> set[str]:
    return {rid for _, output in tool_outputs for rid in _REPORT_ID.findall(output)}


def extract_sources(reply: str, tool_outputs: list[tuple[str, str]]) -> list[Source]:
    pages = retrieved_brochure_pages(tool_outputs)
    reports = retrieved_report_ids(tool_outputs)
    sources = [Source("brochure", doc, page, (doc, page) in pages) for doc, page in cited_brochure_pages(reply)]
    sources += [Source("service_report", rid, None, rid in reports) for rid in cited_report_ids(reply)]
    return sources

"""Brochure retrieval: page-level chunks ranked with BM25.

Keyword ranking is enough for 11 short brochures and keeps the system dependency-free and
explainable. For a large catalogue you would swap `BM25Index` for embeddings + a vector store
(and keep the same `search()` interface), ideally with hybrid keyword + vector scoring.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import config

_TOKEN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
_STOP = set("a an and are as at be by for from in is it of on or that the this to with your you we our".split())


def tokenize(text: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(text.lower()):
        if tok in _STOP:
            continue
        if len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]  # crude plural folding: filters -> filter
        out.append(tok)
        if "-" in tok:  # "ro-500" also matches "ro" and "500"
            out.extend(p for p in tok.split("-") if p)
    return out


@dataclass
class Chunk:
    doc_id: str
    product: str
    edition: str | None
    page: int
    text: str


def load_chunks(brochure_dir: Path = config.BROCHURE_DIR) -> list[Chunk]:
    chunks = []
    for path in sorted(brochure_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        edition = re.search(r"Edition (\d{4})", raw)
        pages = re.split(r"^## Page (\d+)\s*$", raw, flags=re.M)
        # pages = [header, "1", text1, "2", text2, ...]
        first_text = pages[2] if len(pages) > 2 else raw
        product = next((line.strip() for line in first_text.splitlines() if line.strip()), path.stem)
        for num, text in zip(pages[1::2], pages[2::2]):
            if text.strip():
                chunks.append(Chunk(path.stem, product, edition.group(1) if edition else None, int(num), text.strip()))
    return chunks


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        # Doc id and product name are indexed with the page so "PF-DS"-style names always match.
        self.docs = [Counter(tokenize(f"{c.doc_id} {c.product} {c.text}")) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = sum(self.lengths) / max(len(self.lengths), 1)
        df = Counter(t for d in self.docs for t in d)
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, top_k: int = 5, doc_id: str | None = None) -> list[tuple[float, Chunk]]:
        terms = tokenize(query)
        scored = []
        for i, (doc, length) in enumerate(zip(self.docs, self.lengths)):
            chunk = self.chunks[i]
            if doc_id and chunk.doc_id != doc_id:
                continue
            score = 0.0
            for t in terms:
                tf = doc.get(t, 0)
                if tf:
                    norm = self.k1 * (1 - self.b + self.b * length / self.avg_len)
                    score += self.idf[t] * tf * (self.k1 + 1) / (tf + norm)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda x: -x[0])
        return scored[:top_k]


@lru_cache(maxsize=1)
def default_index() -> BM25Index:
    return BM25Index(load_chunks())


def catalog() -> list[dict]:
    """One entry per brochure: what the product is, from its first page."""
    seen = {}
    for c in default_index().chunks:
        if c.doc_id in seen:
            continue
        lines = [line.strip() for line in c.text.splitlines() if line.strip()]
        seen[c.doc_id] = {
            "doc_id": c.doc_id,
            "product": c.product,
            "edition": c.edition,
            "summary": " ".join(lines[1:4])[:300],
        }
    return list(seen.values())

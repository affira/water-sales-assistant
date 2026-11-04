"""Deterministic country-name handling for service records and cost lookups."""
from __future__ import annotations

from typing import Dict, List, Optional, Set

# Different spellings of the same country, mapped to the short names the reports mostly use.
ALIASES: Dict[str, str] = {
    "united arab emirates": "UAE", "uae": "UAE",
    "united states": "USA", "united states of america": "USA", "us": "USA", "usa": "USA",
    "united kingdom": "UK", "great britain": "UK", "england": "UK", "uk": "UK",
    "kingdom of saudi arabia": "Saudi Arabia", "ksa": "Saudi Arabia",
}
_EMPTY = {"", "null", "none", "n/a", "unknown"}

INFERRED_FLAG = "country not stated in the report; taken from this customer's other reports"


def normalize_country(name: Optional[str]) -> str:
    text = (name or "").strip()
    if text.lower() in _EMPTY:
        return ""
    return ALIASES.get(text.lower(), text)


def same_country(a: Optional[str], b: Optional[str]) -> bool:
    na, nb = normalize_country(a), normalize_country(b)
    return bool(na) and na.lower() == nb.lower()


def _known_countries(records: List[dict]) -> Dict[str, Set[str]]:
    known: Dict[str, Set[str]] = {}
    for r in records:
        if r["country"]:
            known.setdefault(r.get("customer", "").strip().lower(), set()).add(r["country"])
    return known


def harmonize_countries(records: List[dict]) -> List[dict]:
    """Normalise country names, then fill a blank country when the same customer's other reports
    all agree on one. Filled values are flagged in `checks`, never silently invented."""
    for r in records:
        r["country"] = normalize_country(r.get("country"))
    known = _known_countries(records)
    for r in records:
        candidates = known.get(r.get("customer", "").strip().lower(), set())
        if not r["country"] and len(candidates) == 1:
            r["country"] = next(iter(candidates))
            checks = r.setdefault("checks", [])
            if INFERRED_FLAG not in checks:
                checks.append(INFERRED_FLAG)
    return records

"""Turn free-text field service reports into structured records (run once, offline).

Division of labour: the LLM (whichever provider is selected) *reads* each messy report into a
strict JSON schema; plain Python then *checks* the arithmetic and flags anything suspicious.
The cost estimator only ever does maths on these records, never on raw text.
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from . import config
from .countries import harmonize_countries
from .providers import Provider, get_provider


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


LINE_ITEM = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "category": {"type": "string", "enum": ["labour", "travel", "part", "consumable", "other"]},
        "quantity": _nullable({"type": "number"}),
        "unit_price": _nullable({"type": "number"}),
        "line_total": _nullable({"type": "number"}),
    },
    "required": ["description", "category", "quantity", "unit_price", "line_total"],
    "additionalProperties": False,
}

RECORD_SCHEMA = {
    "type": "object",
    "properties": {
        "report_id": {"type": "string"},
        "visit_date": _nullable({"type": "string", "description": "ISO yyyy-mm-dd"}),
        "date_ambiguous": {"type": "boolean"},
        "customer": {"type": "string"},
        "site": {"type": "string"},
        "country": {"type": "string"},
        "equipment_family": {"type": "string", "enum": config.EQUIPMENT_FAMILIES},
        "equipment_model": _nullable({"type": "string"}),
        "task_type": {"type": "string", "enum": config.TASK_TYPES},
        "main_quantity": _nullable({"type": "integer"}),
        "onsite_hours": _nullable({"type": "number"}),
        "travel_hours": _nullable({"type": "number"}),
        "currency": _nullable({"type": "string", "description": "ISO 4217 code"}),
        "labour_rate": _nullable({"type": "number"}),
        "travel_rate": _nullable({"type": "number"}),
        "weekend_or_overtime": {"type": "boolean"},
        "parts_supplied_by_customer": {"type": "boolean"},
        "vat_rate_pct": _nullable({"type": "number"}),
        "cost_status": {"type": "string", "enum": ["itemized", "total_only", "truncated", "not_stated"]},
        "stated_total": _nullable({"type": "number"}),
        "line_items": {"type": "array", "items": LINE_ITEM},
        "notes": {"type": "string"},
    },
    "required": [
        "report_id", "visit_date", "date_ambiguous", "customer", "site", "country", "equipment_family",
        "equipment_model", "task_type", "main_quantity", "onsite_hours", "travel_hours", "currency",
        "labour_rate", "travel_rate", "weekend_or_overtime", "parts_supplied_by_customer", "vat_rate_pct",
        "cost_status", "stated_total", "line_items", "notes",
    ],
    "additionalProperties": False,
}

EXTRACTION_PROMPT = """You are extracting data from a field service report written by a technician at \
Norvale Water Technologies. Reports vary from neat tables to rough notes. Record only what the report \
says; use null when a value is not stated. Do not estimate or fill gaps.

Field rules:
- equipment_family: the product line serviced (ClarionRO = RO skid; HPX = high-pressure pump; \
UltraGuard = UF; LumenUV = UV; DosePro = metering pumps; HydraBed = multimedia filter; \
CarbonGuard = carbon filter; SoftLine = softener; AquaTune = control software).
- task_type: the main job. Replacing RO membrane elements = membrane_replacement.
- main_quantity: count of the main units worked on (elements, modules, lamps, pumps, vessels), \
null if not applicable or not stated.
- onsite_hours / travel_hours: as written (round-trip travel total). "Half a day" or similar vague \
phrases -> null, and mention them in notes.
- currency: ISO code. If only "$" is used, infer from the country and say so in notes.
- labour_rate / travel_rate: hourly rates only when shown.
- cost_status: itemized = line items with a total; total_only = a single total figure; \
truncated = the cost table is cut off before the total; not_stated = "invoice to follow", \
"as per agreement" or no figure.
- stated_total: the final amount due exactly as written (incl. VAT if the report includes VAT), \
else null.
- line_items: only the rows of the cost table (no subtotal / VAT / total rows). Use [] if none. \
For a truncated table include only complete rows.
- visit_date: convert to ISO. If the format is dd/mm/yy or mm/dd/yy and the day is <= 12, \
set date_ambiguous true and assume day-first.
- notes: one or two sentences on anything unusual (customer-supplied parts, weekend rates, \
data-entry oddities, vague times)."""


def _extract_one(provider: Provider, path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    user = f"<report file=\"{path.name}\">\n{text}\n</report>"
    data = provider.complete_json(EXTRACTION_PROMPT, user, RECORD_SCHEMA)
    data["source_file"] = path.name
    data["source_sha1"] = hashlib.sha1(text.encode()).hexdigest()
    data["checks"] = validate(data)
    return data


def validate(r: dict) -> list[str]:
    """Deterministic sanity checks on an extracted record. Returns human-readable flags."""
    flags = []
    items = r.get("line_items") or []
    for li in items:
        q, p, t = li.get("quantity"), li.get("unit_price"), li.get("line_total")
        if None not in (q, p, t) and abs(q * p - t) > max(1.0, 0.01 * abs(t)):
            flags.append(f"line '{li['description']}': {q} x {p} != {t}")
    if items and r.get("stated_total") is not None:
        s = sum(li["line_total"] or 0 for li in items)
        expected = s * (1 + (r.get("vat_rate_pct") or 0) / 100)
        if abs(expected - r["stated_total"]) > max(2.0, 0.01 * r["stated_total"]):
            flags.append(f"line items sum to {expected:,.2f} but stated total is {r['stated_total']:,.2f}")
    if r.get("onsite_hours") and r.get("travel_hours") and r["onsite_hours"] == r["travel_hours"]:
        flags.append("travel_hours equals onsite_hours (possible copy-paste error); travel excluded from stats")
    if r.get("cost_status") == "truncated":
        flags.append("cost table truncated; total reconstructed from visible lines is a lower bound")
    if r.get("date_ambiguous"):
        flags.append("date format ambiguous (assumed day-first)")
    return flags


def extract_all(force: bool = False, workers: int = 4, provider: Optional[Provider] = None) -> list[dict]:
    """Extract every report, reusing cached records whose source text has not changed."""
    provider = provider if provider is not None else get_provider()
    config.INDEX_DIR.mkdir(exist_ok=True)
    cached = {}
    if config.RECORDS_PATH.exists() and not force:
        cached = {r["source_file"]: r for r in json.loads(config.RECORDS_PATH.read_text())}
    paths = sorted(config.REPORT_DIR.glob("*.txt"))
    todo = [p for p in paths
            if p.name not in cached
            or cached[p.name]["source_sha1"] != hashlib.sha1(p.read_text(encoding="utf-8").encode()).hexdigest()]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rec in pool.map(lambda path: _extract_one(provider, path), todo):
            cached[rec["source_file"]] = rec
            print(f"  extracted {rec['report_id']}: {rec['equipment_family']} / {rec['task_type']}"
                  + (f"  [{'; '.join(rec['checks'])}]" if rec["checks"] else ""))
    records = harmonize_countries([cached[p.name] for p in paths])
    config.RECORDS_PATH.write_text(json.dumps(records, indent=2))
    return records

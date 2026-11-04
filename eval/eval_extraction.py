"""Score the LLM extraction step against the generator's ground truth.

    python -m aquaassist.ingest          # build index/service_records.json first
    python eval/eval_extraction.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ["country", "equipment_family", "task_type", "main_quantity", "onsite_hours", "travel_hours",
          "currency", "cost_status", "stated_total", "weekend_or_overtime", "parts_supplied_by_customer",
          "labour_rate", "travel_rate"]


def applies(field: str, truth: dict) -> bool:
    """Currency is only knowable when the report states a cost; "invoice to follow" notes name none."""
    return not (field == "currency" and truth["cost_status"] == "not_stated")


def same(field, got, want) -> bool:
    if want is None or got is None:
        return want is got
    if isinstance(want, (int, float)) and not isinstance(want, bool):
        return abs(float(got) - float(want)) <= max(0.01 * abs(want), 0.05)
    if isinstance(want, str):
        return got.strip().lower() == want.strip().lower()
    return got == want


def main() -> None:
    truth = {r["report_id"]: r for r in json.loads((ROOT / "eval" / "ground_truth_service_records.json").read_text())}
    got = {r["report_id"]: r for r in json.loads((ROOT / "index" / "service_records.json").read_text())}
    misses = []
    print(f"{'field':28} accuracy")
    for f in FIELDS:
        ok = scored = 0
        for rid, t in truth.items():
            if not applies(f, t):
                continue
            scored += 1
            g = got.get(rid, {})
            if same(f, g.get(f), t.get(f)):
                ok += 1
            else:
                misses.append((rid, f, g.get(f), t.get(f)))
        note = f"  ({len(truth) - scored} n/a)" if scored < len(truth) else ""
        print(f"{f:28} {ok}/{scored}{note}")
    if misses:
        print("\nMismatches (report, field, extracted, truth):")
        for m in misses:
            print("  ", *m)


if __name__ == "__main__":
    main()

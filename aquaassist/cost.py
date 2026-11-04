"""Service-visit cost estimation from historical field service records.

The estimate is assembled from components, each taken from the most relevant evidence:

    on-site hours  <- same equipment + same task (scaled by quantity when possible)
    travel hours   <- user-supplied, else visits in the same country (any equipment)
    hourly rates   <- itemized reports from the same country, else all itemized reports
    main part cost <- unit prices from itemized reports of the same task
    other parts    <- remaining parts/consumables on those reports

Every number in the output carries the report IDs it came from, plus explicit caveats for
whatever had to be borrowed from a less similar context. No LLM is involved here.
"""
from __future__ import annotations

import json
import statistics
from typing import Iterable

import numpy as np

from . import config
from .countries import same_country

USABLE_COST = ("itemized", "truncated")


def load_records() -> list[dict]:
    if not config.RECORDS_PATH.exists():
        raise FileNotFoundError("No extracted records found. Run `python -m aquaassist.ingest` first.")
    return json.loads(config.RECORDS_PATH.read_text())


def to_usd(amount: float, currency: str | None) -> float | None:
    if amount is None or currency not in config.FX_TO_USD:
        return None
    return amount * config.FX_TO_USD[currency]


def _spread(values: Iterable[float]) -> dict | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"low": min(vals), "typical": statistics.median(vals), "high": max(vals), "n": len(vals)}


def _country_match(record: dict, country: str | None) -> bool:
    return same_country(record.get("country"), country)


def _travel_ok(r: dict) -> bool:
    return r.get("travel_hours") is not None and not any("travel_hours equals onsite" in c for c in r.get("checks", []))


def _main_part_lines(r: dict) -> tuple[list[dict], list[dict]]:
    """Split a record's part/consumable lines into (main part lines, other lines).

    The main part is the line whose quantity equals the job's main quantity (e.g. 12 elements).
    """
    parts = [li for li in r.get("line_items") or [] if li["category"] in ("part", "consumable")]
    q = r.get("main_quantity")
    main = [li for li in parts if q and li["category"] == "part" and li.get("quantity") == q]
    other = [li for li in parts if li not in main]
    return main, other


def estimate(equipment_family: str, task_type: str, quantity: int | None = None, country: str | None = None,
             travel_hours: float | None = None, output_currency: str = "USD",
             records: list[dict] | None = None) -> dict:
    records = records if records is not None else load_records()
    caveats: list[str] = []
    similar = [r for r in records if r["equipment_family"] == equipment_family and r["task_type"] == task_type]
    if not similar:
        tasks_seen = sorted({r["task_type"] for r in records if r["equipment_family"] == equipment_family})
        return {
            "status": "no_matching_history",
            "message": f"No historical reports for {task_type} on {equipment_family}.",
            "tasks_with_history_for_this_equipment": tasks_seen,
        }

    in_country = [r for r in similar if _country_match(r, country)]
    if country and not in_country:
        caveats.append(f"No {equipment_family} {task_type} visits recorded in {country}; "
                       f"job effort is taken from {len(similar)} visits in other countries.")

    # --- on-site hours ------------------------------------------------------
    points = [(r["main_quantity"], r["onsite_hours"]) for r in similar
              if r.get("onsite_hours") is not None and r.get("main_quantity")]
    if quantity and len({q for q, _ in points}) >= 2:
        qs, hs = np.array([p[0] for p in points], float), np.array([p[1] for p in points], float)
        slope, intercept = np.polyfit(qs, hs, 1)
        slope = max(slope, 0.0)
        intercept = float(np.mean(hs - slope * qs))
        fitted = intercept + slope * qs
        ratios = hs / fitted
        typical = intercept + slope * quantity
        hours = {"low": typical * ratios.min(), "typical": typical, "high": typical * ratios.max(), "n": len(points)}
        hours_method = (f"linear fit over {len(points)} visits: {intercept:.1f} h + {slope:.2f} h per unit "
                        f"(quantities seen {int(qs.min())}-{int(qs.max())})")
        if quantity > qs.max() or quantity < qs.min():
            caveats.append(f"Requested quantity {quantity} is outside the range seen ({int(qs.min())}-{int(qs.max())}); "
                           "hours are extrapolated.")
    else:
        hours = _spread(r.get("onsite_hours") for r in similar)
        hours_method = f"median of {hours['n']} similar visits" if hours else "no hours recorded"
        if quantity and points:
            caveats.append("Not enough variation in past quantities to scale hours by quantity.")
    hours_ids = [r["report_id"] for r in similar if r.get("onsite_hours") is not None]

    # --- travel hours -------------------------------------------------------
    if travel_hours is not None:
        travel = {"low": travel_hours, "typical": travel_hours, "high": travel_hours, "n": 0}
        travel_method, travel_ids = "provided by user", []
    else:
        local = [r for r in records if _country_match(r, country) and _travel_ok(r)]
        pool = local or [r for r in records if _travel_ok(r)]
        travel = _spread(r["travel_hours"] for r in pool)
        travel_ids = [r["report_id"] for r in pool]
        if local:
            sites = sorted({r["site"] for r in local})
            travel_method = f"round-trip travel on {len(local)} visits in {country} ({'; '.join(sites)})"
            if len(sites) > 1:
                caveats.append(f"Travel varies by site within {country}: ask how far the customer site is "
                               "from the service base to tighten the range.")
        else:
            travel_method = f"all {len(pool)} visits worldwide (no visits recorded in {country or 'that country'})"
            caveats.append("Travel time is a global average; ask where the site is to tighten it.")

    # --- hourly rates (USD) -------------------------------------------------
    def rate_spread(field: str):
        rated = [r for r in records if r.get("cost_status") in USABLE_COST and r.get(field)
                 and not r.get("weekend_or_overtime")]
        local = [r for r in rated if _country_match(r, country)]
        pool = local or rated
        return _spread(to_usd(r[field], r["currency"]) for r in pool), [r["report_id"] for r in pool], bool(local)

    labour_rate, labour_ids, labour_local = rate_spread("labour_rate")
    travel_rate, travel_rate_ids, _ = rate_spread("travel_rate")
    rate_basis = f"itemized reports in {country}" if labour_local else "all itemized reports (no rates on file for this country)"
    if not labour_local:
        caveats.append("No itemized rates on file for this country; using rates from other countries, "
                       "which vary widely (check the local rate card).")

    # --- parts --------------------------------------------------------------
    priced = [r for r in similar if r.get("cost_status") in USABLE_COST]
    unit_prices, other_parts, parts_ids = [], [], []
    for r in priced:
        main, other = _main_part_lines(r)
        for li in main:
            if li.get("unit_price"):
                unit_prices.append(to_usd(li["unit_price"], r["currency"]))
            elif r.get("parts_supplied_by_customer"):
                caveats.append(f"{r['report_id']}: parts were customer-supplied (zero cost) and are excluded from part prices.")
        if not (r.get("cost_status") == "truncated"):  # truncated tables may be missing lines
            other_parts.append(sum(to_usd(li["line_total"] or 0, r["currency"]) for li in other))
        parts_ids.append(r["report_id"])
    unit = _spread(unit_prices)
    other = _spread(other_parts) or {"low": 0, "typical": 0, "high": 0, "n": 0}
    if quantity and not unit and any(r.get("main_quantity") for r in similar):
        caveats.append("No itemized unit price for the main part in past reports; parts cost is incomplete.")
    if not priced:
        caveats.append("None of the similar reports itemize costs, so parts cost is unknown.")

    # --- assemble -----------------------------------------------------------
    def total(level: str) -> float | None:
        if not (hours and labour_rate and travel and travel_rate):
            return None
        t = hours[level] * labour_rate[level] + travel[level] * travel_rate[level] + other[level]
        if quantity and unit:
            t += quantity * unit[level]
        return t

    fx = config.FX_TO_USD.get(output_currency.upper())
    if fx is None:
        caveats.append(f"Unknown currency {output_currency}; reporting in USD.")
        output_currency, fx = "USD", 1.0

    def conv(x):
        return None if x is None else round(x / fx, 0)

    def conv_spread(s):
        return None if s is None else {k: (conv(v) if k != "n" else v) for k, v in s.items()}

    # Cross-check: what similar jobs actually cost in total (any cost status with a figure).
    actuals = []
    for r in similar:
        amount = r.get("stated_total")
        if amount is not None and r.get("vat_rate_pct"):
            amount = amount / (1 + r["vat_rate_pct"] / 100)
        if amount is None and r.get("cost_status") == "truncated":
            continue
        if amount is not None:
            actuals.append({"report_id": r["report_id"], "country": r["country"], "quantity": r.get("main_quantity"),
                            "total_ex_vat": conv(to_usd(amount, r["currency"])), "original": f"{r['currency']} {amount:,.0f}"})
    no_cost = [r["report_id"] for r in similar if r.get("cost_status") == "not_stated"]
    if no_cost:
        caveats.append(f"{len(no_cost)} similar reports state no cost ({', '.join(no_cost)}); "
                       "used for hours only.")
    for r in similar:
        for c in r.get("checks", []):
            if "truncated" in c or "copy-paste" in c:
                caveats.append(f"{r['report_id']}: {c}.")

    return {
        "status": "ok",
        "request": {"equipment_family": equipment_family, "task_type": task_type, "quantity": quantity,
                    "country": country, "travel_hours": travel_hours},
        "currency": output_currency,
        "estimate": {"low": conv(total("low")), "typical": conv(total("typical")), "high": conv(total("high"))},
        "components": {
            "onsite_hours": {**{k: round(v, 1) for k, v in hours.items() if k != "n"}, "method": hours_method,
                             "reports": hours_ids} if hours else None,
            "travel_hours": {**{k: round(v, 1) for k, v in travel.items() if k != "n"}, "method": travel_method,
                             "reports": travel_ids} if travel else None,
            "labour_rate_per_h": {**conv_spread(labour_rate), "basis": rate_basis, "reports": labour_ids} if labour_rate else None,
            "travel_rate_per_h": {**conv_spread(travel_rate), "reports": travel_rate_ids} if travel_rate else None,
            "main_part_unit_price": {**conv_spread(unit), "reports": parts_ids} if unit else None,
            "other_parts_and_consumables": conv_spread(other),
        },
        "similar_jobs_actual_totals": actuals,
        "caveats": caveats,
        "fx_assumption": config.FX_NOTE,
    }


def find_reports(equipment_family: str | None = None, task_type: str | None = None, country: str | None = None,
                 records: list[dict] | None = None) -> list[dict]:
    records = records if records is not None else load_records()
    out = []
    for r in records:
        if equipment_family and r["equipment_family"] != equipment_family:
            continue
        if task_type and r["task_type"] != task_type:
            continue
        if country and not _country_match(r, country):
            continue
        out.append({k: r.get(k) for k in ("report_id", "visit_date", "customer", "country", "equipment_family",
                                          "equipment_model", "task_type", "main_quantity", "onsite_hours",
                                          "travel_hours", "currency", "stated_total", "cost_status", "checks")})
    return out

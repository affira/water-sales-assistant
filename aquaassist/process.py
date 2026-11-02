"""Compare a customer's RO readings with one reference plant's hourly history.

All numbers come from pandas over `data/process/ro_plant_hourly.csv`; the LLM only explains them.
Cleaning is explicit and reported back, because how much to trust the comparison depends on it.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from . import config

METRICS = {
    "feed_temperature_C": ("Feed temperature", "°C", None),
    "feed_conductivity_uS_cm": ("Feed conductivity", "µS/cm", None),
    "feed_turbidity_NTU": ("Feed turbidity", "NTU", "higher"),
    "feed_pH": ("Feed pH", "", None),
    "feed_SDI15": ("Feed SDI15", "", "higher"),
    "antiscalant_dose_mg_L": ("Antiscalant dose", "mg/L", None),
    "feed_pressure_bar": ("Feed pressure", "bar", "higher"),
    "differential_pressure_bar": ("Differential pressure", "bar", "higher"),
    "permeate_flow_m3h": ("Permeate flow", "m3/h", "lower"),
    "recovery_pct": ("Recovery", "%", None),
    "permeate_conductivity_uS_cm": ("Permeate conductivity", "µS/cm", "higher"),
    "salt_passage_pct": ("Salt passage (derived)", "%", "higher"),
}
# Operating context: hours are matched on these before comparing outcomes.
CONTEXT = {"recovery_pct": 2.0, "feed_conductivity_uS_cm": 0.15, "feed_temperature_C": 3.0}
OUTCOMES = ["permeate_conductivity_uS_cm", "salt_passage_pct", "differential_pressure_bar", "feed_pressure_bar"]


def salt_passage(perm_cond, feed_cond, recovery_pct):
    """Salt passage vs. the average feed-concentrate concentration (standard RO approximation)."""
    r = np.asarray(recovery_pct, float) / 100
    conc_factor = np.log(1 / (1 - r)) / r
    return 100 * np.asarray(perm_cond, float) / (np.asarray(feed_cond, float) * conc_factor)


@lru_cache(maxsize=1)
def load() -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(config.PROCESS_CSV, parse_dates=["timestamp"])
    df = raw.copy()
    notes = {"rows_total": len(raw), "period": f"{raw.timestamp.min():%Y-%m-%d} to {raw.timestamp.max():%Y-%m-%d}"}

    offline = df["permeate_flow_m3h"] < 1
    notes["rows_removed_plant_offline"] = int(offline.sum())
    df = df[~offline].copy()

    # A sensor that repeats the exact same value for 6+ hours is stuck, not measuring.
    pc = df["permeate_conductivity_uS_cm"]
    run_id = (pc != pc.shift()).cumsum()
    run_len = pc.groupby(run_id).transform("size")
    stuck = (run_len >= 6) & pc.notna()
    notes["hours_permeate_sensor_stuck"] = int(stuck.sum())
    df.loc[stuck, "permeate_conductivity_uS_cm"] = np.nan

    notes["missing_values"] = {c: int(df[c].isna().sum()) for c in df.columns if df[c].isna().any()}
    notes["sdi_note"] = "SDI15 is a manual lab test every 8 h carried forward, so it is not truly hourly."
    df["salt_passage_pct"] = salt_passage(df.permeate_conductivity_uS_cm, df.feed_conductivity_uS_cm, df.recovery_pct)
    notes["rows_used"] = len(df)
    return df, notes


def _describe(series: pd.Series, value: float, worse: str | None) -> dict:
    s = series.dropna()
    pct = float((s < value).mean() * 100)
    out = {
        "customer_value": value,
        "percentile_in_reference": round(pct, 1),
        "reference_p10": round(float(s.quantile(0.10)), 2),
        "reference_median": round(float(s.median()), 2),
        "reference_p90": round(float(s.quantile(0.90)), 2),
        "reference_min": round(float(s.min()), 2),
        "reference_max": round(float(s.max()), 2),
        "hours_compared": int(len(s)),
    }
    if worse == "higher":
        out["share_of_reference_hours_as_bad_or_worse_pct"] = round(float((s >= value).mean() * 100), 1)
    elif worse == "lower":
        out["share_of_reference_hours_as_bad_or_worse_pct"] = round(float((s <= value).mean() * 100), 1)
    if value > s.max() or value < s.min():
        out["warning"] = "Value is outside everything observed in the reference plant."
    return out


def analyze(readings: dict) -> dict:
    df, notes = load()
    readings = {k: v for k, v in readings.items() if v is not None and k in METRICS}
    if {"permeate_conductivity_uS_cm", "feed_conductivity_uS_cm", "recovery_pct"} <= readings.keys() \
            and "salt_passage_pct" not in readings:
        readings["salt_passage_pct"] = round(float(salt_passage(readings["permeate_conductivity_uS_cm"],
                                                               readings["feed_conductivity_uS_cm"],
                                                               readings["recovery_pct"])), 2)
    if not readings:
        return {"status": "no_readings", "message": "Provide at least one reading.", "known_metrics": list(METRICS)}

    result = {"status": "ok", "reference_dataset": {
        "description": "One brackish-water ClarionRO-500 plant, hourly averages (synthetic demo data).",
        **notes}}

    result["vs_all_hours"] = {k: {"metric": METRICS[k][0], "unit": METRICS[k][1], **_describe(df[k], v, METRICS[k][2])}
                              for k, v in readings.items()}

    # Like-for-like: only hours with a similar operating context.
    ctx = {k: v for k, v in readings.items() if k in CONTEXT}
    if ctx:
        mask = pd.Series(True, index=df.index)
        for k, v in ctx.items():
            tol = CONTEXT[k] * v if k == "feed_conductivity_uS_cm" else CONTEXT[k]
            mask &= (df[k] - v).abs() <= tol
        sub = df[mask]
        cond = {k: f"{v} ± {CONTEXT[k] * v if k == 'feed_conductivity_uS_cm' else CONTEXT[k]:g}" for k, v in ctx.items()}
        block = {"matching_conditions": cond, "matching_hours": int(len(sub))}
        if len(sub) >= 50:
            block["outcomes"] = {k: {"metric": METRICS[k][0], **_describe(sub[k], readings[k], METRICS[k][2])}
                                 for k in OUTCOMES if k in readings}
        else:
            block["warning"] = "Fewer than 50 matching hours; like-for-like comparison not reliable."
        result["vs_similar_operating_hours"] = block

    # What distinguishes the reference plant's worst permeate-quality hours?
    target = "salt_passage_pct"
    worst = df[df[target] >= df[target].quantile(0.9)]
    rest = df[df[target] < df[target].quantile(0.5)]
    drivers = {}
    for k in ["feed_temperature_C", "recovery_pct", "antiscalant_dose_mg_L", "differential_pressure_bar",
              "feed_pressure_bar", "feed_turbidity_NTU", "feed_SDI15", "feed_conductivity_uS_cm"]:
        drivers[k] = {
            "median_in_worst_10pct_hours": round(float(worst[k].median()), 2),
            "median_in_best_50pct_hours": round(float(rest[k].median()), 2),
            "spearman_corr_with_salt_passage": round(float(df[[k, target]].corr(method="spearman").iloc[0, 1]), 2),
        }
    by_month = worst.groupby(worst.timestamp.dt.strftime("%Y-%m")).size()
    result["reference_plant_drivers_of_high_salt_passage"] = {
        "comparison": drivers,
        "worst_hours_by_month": {m: int(n) for m, n in by_month.items()},
        "caution": "Correlation in one plant's history, not proven cause; drivers move together "
                   "(e.g. warm months, fouling between cleanings).",
    }
    result["trust_notes"] = [
        "Single reference plant; the customer's membranes, feed chemistry and design may differ.",
        "Hourly averages hide short events; the comparison says how unusual a reading is, not why.",
        f"Cleaning applied: {notes['rows_removed_plant_offline']} offline hours removed, "
        f"{notes['hours_permeate_sensor_stuck']} stuck-sensor hours blanked.",
    ]
    return result

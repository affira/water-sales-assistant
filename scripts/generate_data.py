"""Generate the synthetic dataset for the Norvale Water Technologies sales assistant.

Everything here is fictional. Run once (output is deterministic thanks to fixed seeds):

    python scripts/generate_data.py

Writes:
  data/service_reports/FSR-0001.txt ... FSR-0050.txt   messy technician reports
  data/process/ro_plant_hourly.csv                    hourly RO plant log
  eval/ground_truth_service_records.json              what each report *really* says,
                                                      used to score the LLM extraction step
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "data" / "service_reports"
PROCESS_DIR = ROOT / "data" / "process"
EVAL_DIR = ROOT / "eval"

rng = random.Random(42)

# ---------------------------------------------------------------------------
# Reference tables
# ---------------------------------------------------------------------------

# Rough FX used only to price parts in local currency (USD per 1 unit of currency).
FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "SAR": 0.2667, "AED": 0.2723,
             "AUD": 0.66, "INR": 0.012, "ZAR": 0.054, "MXN": 0.054}

SITES = {
    "S1": dict(customer="Kestrel Beverage Co.", site="Fort Worth, USA", country="USA", currency="USD",
               labour=135, travel=90, travel_h=3.0),
    "S2": dict(customer="Gulf Coast Polymers", site="Corpus Christi, USA", country="USA", currency="USD",
               labour=135, travel=90, travel_h=5.5),
    "S3": dict(customer="Agroindustrias Sonora", site="Hermosillo, Mexico", country="Mexico", currency="USD",
               labour=92, travel=65, travel_h=6.0),
    "S4": dict(customer="Desalinas del Norte S.A.", site="Antofagasta, Chile", country="Chile", currency="USD",
               labour=105, travel=75, travel_h=9.5),
    "S5": dict(customer="Valle Verde Foods", site="Rancagua, Chile", country="Chile", currency="USD",
               labour=105, travel=75, travel_h=3.5),
    "S6": dict(customer="Aguas de Levante", site="Alicante, Spain", country="Spain", currency="EUR",
               labour=82, travel=58, travel_h=4.0),
    "S7": dict(customer="Rheinwerk Chemie GmbH", site="Ludwigshafen, Germany", country="Germany", currency="EUR",
               labour=104, travel=70, travel_h=2.5),
    "S8": dict(customer="Thames Valley Pharma Ltd", site="Reading, UK", country="UK", currency="GBP",
               labour=88, travel=60, travel_h=2.0),
    "S9": dict(customer="Al Noor Industrial Water", site="Jubail, Saudi Arabia", country="Saudi Arabia",
               currency="SAR", labour=420, travel=280, travel_h=7.0),
    "S10": dict(customer="Desert Bloom District Cooling", site="Dubai, UAE", country="UAE", currency="AED",
                labour=390, travel=260, travel_h=3.0),
    "S11": dict(customer="Pilbara Mine Water Pty Ltd", site="Port Hedland, Australia", country="Australia",
                currency="AUD", labour=155, travel=98, travel_h=11.0),
    "S12": dict(customer="Coromandel Textiles", site="Tiruppur, India", country="India", currency="INR",
                labour=5200, travel=3300, travel_h=6.5),
    "S13": dict(customer="Karoo Municipal Water", site="Beaufort West, South Africa", country="South Africa",
                currency="ZAR", labour=1650, travel=1100, travel_h=8.0),
}

TECHS = ["Marta Quintero", "Daniel Okafor", "Lena Hartmann", "Rafael Souza", "Priya Raman",
         "Tom Whitfield", "Yusuf Haddad", "Chloe Bennett", "Mateo Fernández", "Ingrid Solberg"]

# Parts catalogue in USD (converted to the report's currency at generation time).
PARTS = {
    "BW-400": ("PureMem BW-400 RO element (8040)", 640),
    "FR-400": ("PureMem FR-400 fouling-resistant RO element (8040)", 760),
    "LE-440": ("PureMem LE-440 low-energy RO element (8040)", 690),
    "ORING": ("Interconnector & O-ring kit (per vessel)", 45),
    "ACID": ("Acid cleaner CL-A, 25 L", 140),
    "ALK": ("Alkaline cleaner CL-B, 25 L", 165),
    "UFMOD": ("UltraGuard UG-M80 UF module", 1850),
    "UVLAMP": ("LumenUV amalgam lamp", 310),
    "SLEEVE": ("LumenUV quartz sleeve", 190),
    "DIA10": ("DosePro DP-10 diaphragm kit", 120),
    "DIA50": ("DosePro DP-50 diaphragm kit", 210),
    "SEAL": ("HPX mechanical seal cartridge", 1450),
    "BEARING": ("HPX bearing set", 380),
    "ANTH": ("Anthracite media, 25 kg bag", 38),
    "SAND": ("Filter sand media, 25 kg bag", 14),
    "GARNET": ("Garnet media, 25 kg bag", 42),
    "CARBON": ("Activated carbon media, 25 kg bag", 85),
    "RESIN": ("SAC softener resin, 25 L bag", 95),
    "VALVEKIT": ("SoftLine control valve rebuild kit", 260),
    "EDGE": ("AquaTune edge controller", 6200),
    "IOKIT": ("AquaTune I/O retrofit kit", 2400),
    "SWKIT": ("AquaTune software update licence", 1900),
    "CALSOL": ("Conductivity calibration solution set", 35),
    "PPE": ("PPE & consumables (gloves, wipes, lubricant)", 25),
}

# task key -> (equipment_family, task_type, base_hours, hours_per_unit, unit_label)
TASKS = {
    "ro_membranes": ("ClarionRO", "membrane_replacement", 3.0, 0.30, "membrane elements"),
    "ro_cip": ("ClarionRO", "cip_cleaning", 6.0, 0.45, "pressure vessels"),
    "ro_cal": ("ClarionRO", "sensor_calibration", 3.5, 0.0, None),
    "ro_emergency": ("ClarionRO", "emergency_repair", 5.0, 0.0, None),
    "ro_inspection": ("ClarionRO", "inspection", 4.0, 0.0, None),
    "hpx_seal": ("HPX", "pump_seal_replacement", 5.0, 0.0, "pump"),
    "hpx_overhaul": ("HPX", "pump_overhaul", 9.5, 0.0, "pump"),
    "uf_modules": ("UltraGuard", "uf_module_replacement", 2.5, 0.9, "UF modules"),
    "uf_integrity": ("UltraGuard", "integrity_test", 4.5, 0.0, None),
    "uv_lamps": ("LumenUV", "uv_lamp_replacement", 1.5, 0.35, "lamps"),
    "dose_dia": ("DosePro", "diaphragm_replacement", 1.0, 0.6, "pumps"),
    "mmf_media": ("HydraBed", "media_replacement", 8.0, 3.5, "filter vessels"),
    "soft_resin": ("SoftLine", "resin_replacement", 4.0, 2.0, "softener vessels"),
    "tune_comm": ("AquaTune", "commissioning", 14.0, 0.0, None),
    "tune_sw": ("AquaTune", "software_update", 6.0, 0.0, None),
    "acf_media": ("CarbonGuard", "media_replacement", 6.0, 3.0, "filter vessels"),
}

# The job plan: (task, site, model, quantity, style, quirks)
# styles: formal (itemised tables) | prose (total only) | shorthand (no cost) | minimal (invoice to follow)
JOBS = [
    ("ro_membranes", "S1", "RO-500", 24, "formal", {"element": "BW-400"}),
    ("ro_membranes", "S6", "RO-500", 12, "formal", {"element": "BW-400", "vat": 21}),
    ("ro_membranes", "S9", "RO-1000", 48, "formal", {"element": "BW-400"}),
    ("ro_membranes", "S11", "RO-250", 12, "prose", {"element": "FR-400", "dollar_only": True}),
    ("ro_membranes", "S3", "RO-500", 8, "prose", {"element": "BW-400"}),
    ("ro_membranes", "S8", "RO-250", 12, "shorthand", {"element": "BW-400"}),
    ("ro_membranes", "S7", "RO-500", 24, "formal", {"element": "LE-440", "truncate": True}),
    ("ro_membranes", "S12", "RO-1000", 18, "formal", {"element": "BW-400", "weekend": True}),
    ("ro_membranes", "S2", "RO-250", 6, "formal", {"element": "BW-400", "customer_parts": True}),
    ("ro_membranes", "S10", "RO-500", 24, "minimal", {"element": "FR-400"}),
    ("ro_membranes", "S13", "RO-500", 12, "prose", {"element": "BW-400"}),
    ("ro_cip", "S5", "RO-250", 2, "formal", {}),
    ("ro_cip", "S4", "RO-1000", 8, "prose", {}),
    ("ro_cip", "S1", "RO-500", 4, "shorthand", {}),
    ("ro_cip", "S13", "RO-500", 4, "formal", {"travel_copy_bug": True}),
    ("ro_cip", "S6", "RO-250", 2, "minimal", {}),
    ("ro_cal", "S3", "RO-500", None, "prose", {}),
    ("ro_cal", "S9", "RO-1000", None, "minimal", {}),
    ("ro_emergency", "S2", "RO-500", None, "prose", {}),
    ("ro_emergency", "S11", "RO-1000", None, "shorthand", {}),
    ("ro_inspection", "S4", "RO-1000", None, "shorthand", {"weekend": True}),
    ("hpx_seal", "S4", "HPX-25", 1, "formal", {}),
    ("hpx_seal", "S10", "HPX-25", 1, "prose", {}),
    ("hpx_seal", "S12", "HPX-50", 1, "shorthand", {}),
    ("hpx_seal", "S7", "HPX-25", 1, "formal", {}),
    ("hpx_overhaul", "S9", "HPX-50", 1, "formal", {}),
    ("hpx_overhaul", "S13", "HPX-25", 1, "minimal", {}),
    ("uf_modules", "S6", "UG-150", 6, "formal", {}),
    ("uf_modules", "S11", "UG-400", 10, "prose", {"dollar_only": True}),
    ("uf_modules", "S1", "UG-50", 4, "minimal", {}),
    ("uf_integrity", "S7", "UG-150", None, "prose", {}),
    ("uf_integrity", "S8", "UG-50", None, "shorthand", {}),
    ("uv_lamps", "S1", "UV-L80", 6, "formal", {}),
    ("uv_lamps", "S5", "UV-L20", 2, "prose", {}),
    ("uv_lamps", "S8", "UV-L250", 16, "formal", {"sleeves": 4}),
    ("uv_lamps", "S10", "UV-L80", 6, "shorthand", {}),
    ("dose_dia", "S3", "DP-10", 4, "formal", {"currency": "MXN"}),
    ("dose_dia", "S12", "DP-50", 2, "prose", {}),
    ("dose_dia", "S2", "DP-10", 3, "minimal", {}),
    ("mmf_media", "S4", "HydraBed 2.4 m", 2, "formal", {}),
    ("mmf_media", "S13", "HydraBed 1.6 m", 3, "prose", {}),
    ("mmf_media", "S9", "HydraBed 3.0 m", 2, "shorthand", {}),
    ("soft_resin", "S5", "SL-10 duplex", 2, "formal", {}),
    ("soft_resin", "S1", "SL-5 duplex", 2, "prose", {}),
    ("soft_resin", "S6", "SL-5 duplex", 2, "shorthand", {}),
    ("tune_comm", "S6", "AquaTune Optimizer", None, "formal", {}),
    ("tune_comm", "S9", "AquaTune Optimizer", None, "prose", {}),
    ("tune_sw", "S11", "AquaTune Optimizer", None, "formal", {}),
    ("tune_sw", "S7", "AquaTune Optimizer", None, "minimal", {}),
    ("acf_media", "S12", "CarbonGuard 2.0 m", 2, "formal", {}),
]
assert len(JOBS) == 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def money(x: float, cur: str) -> str:
    if cur in ("INR", "ZAR", "SAR", "AED", "MXN"):
        return f"{x:,.0f}"
    return f"{x:,.2f}"


def local_price(usd: float, cur: str) -> float:
    p = usd / FX_TO_USD[cur] * rng.uniform(0.95, 1.08)
    return round(p, 0) if cur in ("INR", "ZAR", "SAR", "AED", "MXN") else round(p, 2)


def parts_for(task: str, qty: int | None, quirks: dict) -> list[tuple[str, float]]:
    """Returns [(part_key, quantity)] used on the job."""
    if task == "ro_membranes":
        vessels = max(1, math.ceil(qty / 6))
        return [(quirks["element"], qty), ("ORING", vessels), ("PPE", 1)]
    if task == "ro_cip":
        return [("ACID", 2 + qty // 2), ("ALK", 2 + qty // 2), ("PPE", 1)]
    if task == "ro_cal":
        return [("CALSOL", 2)]
    if task == "ro_emergency":
        return [("ORING", 2), ("PPE", 1)]
    if task == "ro_inspection":
        return [("PPE", 1)]
    if task == "hpx_seal":
        return [("SEAL", 1), ("PPE", 1)]
    if task == "hpx_overhaul":
        return [("SEAL", 1), ("BEARING", 1), ("PPE", 1)]
    if task == "uf_modules":
        return [("UFMOD", qty), ("PPE", 1)]
    if task == "uf_integrity":
        return [("PPE", 1)]
    if task == "uv_lamps":
        out = [("UVLAMP", qty)]
        if quirks.get("sleeves"):
            out.append(("SLEEVE", quirks["sleeves"]))
        return out
    if task == "dose_dia":
        kit = "DIA10" if qty and qty >= 3 else "DIA50"
        return [(kit, qty)]
    if task == "mmf_media":
        return [("ANTH", 12 * qty), ("SAND", 30 * qty), ("GARNET", 8 * qty)]
    if task == "soft_resin":
        return [("RESIN", 8 * qty), ("VALVEKIT", 1)]
    if task == "tune_comm":
        return [("EDGE", 1), ("IOKIT", 1)]
    if task == "tune_sw":
        return [("SWKIT", 1)]
    if task == "acf_media":
        return [("CARBON", 20 * qty), ("PPE", 1)]
    raise KeyError(task)


def job_dates():
    start = date(2023, 1, 9)
    days = sorted(rng.sample(range(0, 700), len(JOBS)))
    return [start + timedelta(days=d) for d in days]


def fmt_date(d: date, style: str) -> tuple[str, bool]:
    """Returns (text, ambiguous)."""
    if style == "formal":
        return d.isoformat(), False
    if style == "prose":
        return d.strftime("%d %B %Y").lstrip("0"), False
    if style == "shorthand":
        ambiguous = d.day <= 12
        return d.strftime("%d/%m/%y"), ambiguous
    return d.isoformat(), False


# ---------------------------------------------------------------------------
# Narrative snippets
# ---------------------------------------------------------------------------

FINDINGS = {
    "ro_membranes": ["Normalized permeate flow had dropped ~18% and salt passage roughly doubled since the last CIP.",
                     "Lead elements showed heavy biofouling and the feed-side spacers were compressed.",
                     "Autopsy of one lead element showed colloidal fouling consistent with poor pretreatment.",
                     "Elements were past 5 years in service; permeate conductivity trending above spec."],
    "ro_cip": ["Normalized dp up ~20% on stage 1, permeate flow down ~12%.",
               "Mixed scaling/organic fouling; acid clean first, then high-pH clean.",
               "Customer reported rising feed pressure over the last 3 weeks."],
    "ro_cal": ["Permeate conductivity probe read 9% high against reference.",
               "Feed flow meter drifted; pressure transmitters within tolerance."],
    "ro_emergency": ["Vessel end-cap O-ring failure causing brine leak at the 2nd stage.",
                     "Permeate tube cracked at interconnector after a pressure surge on restart."],
    "ro_inspection": ["General inspection; minor leak at a sample valve, vessels and frame in good condition."],
    "hpx_seal": ["Mechanical seal leaking at roughly 1 L/h; faces scored.",
                 "Seal failure after dry-running event during feed tank low level."],
    "hpx_overhaul": ["Bearing noise and elevated vibration (7.1 mm/s); seal also near end of life."],
    "uf_modules": ["Pressure decay test failed on several modules — fibre breakage suspected.",
                   "Modules irreversibly fouled after a turbidity event; TMP no longer recovering after CEB."],
    "uf_integrity": ["All racks passed the pressure decay test except one module, isolated for repair."],
    "uv_lamps": ["Lamps at ~12,500 h, UV intensity down to 68% of new.",
                 "Two lamps failed; remaining lamps past rated life."],
    "dose_dia": ["Antiscalant pump output down ~15%; diaphragms stiff and cracked at the edge."],
    "mmf_media": ["Media heavily mud-balled; filtered water turbidity >2 NTU after backwash.",
                  "Anthracite loss of ~20 cm from carry-over during backwash."],
    "soft_resin": ["Resin capacity exhausted early; hardness breakthrough after 60% of design run length."],
    "tune_comm": ["Connected AquaTune to the RO PLC via Modbus TCP; normalization baseline captured."],
    "tune_sw": ["Updated AquaTune to the latest release after feed water source change; retuned dosing model."],
    "acf_media": ["Carbon exhausted — free chlorine breakthrough to RO feed measured at 0.3 mg/L."],
}


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------

def build_job(i: int, job: tuple, d: date) -> tuple[str, dict]:
    task, site_key, model, qty, style, quirks = job
    site = SITES[site_key]
    fam, task_type, base_h, per_unit, unit_label = TASKS[task]
    cur = quirks.get("currency", site["currency"])
    fx_scale = FX_TO_USD[site["currency"]] / FX_TO_USD[cur]  # if a report uses another currency
    tech = rng.choice(TECHS)
    rid = f"FSR-{i:04d}"

    onsite = round((base_h + per_unit * (qty or 0)) * math.exp(rng.gauss(0, 0.12)), 1)
    travel = round(site["travel_h"] * rng.uniform(0.85, 1.15), 1)
    weekend = bool(quirks.get("weekend"))
    lab_rate = round(site["labour"] * fx_scale * rng.uniform(0.96, 1.05) * (1.5 if weekend else 1.0), 0)
    trav_rate = round(site["travel"] * fx_scale * rng.uniform(0.96, 1.05), 0)

    parts = parts_for(task, qty, quirks)
    customer_parts = bool(quirks.get("customer_parts"))
    lines = [
        dict(description="Travel", category="travel", quantity=travel, unit_price=trav_rate,
             line_total=round(travel * trav_rate, 2)),
        dict(description="Labour (weekend rate)" if weekend else "Labour", category="labour",
             quantity=onsite, unit_price=lab_rate, line_total=round(onsite * lab_rate, 2)),
    ]
    for key, q in parts:
        name, usd = PARTS[key]
        price = local_price(usd, cur)
        cat = "consumable" if key in ("PPE", "CALSOL", "ACID", "ALK") else "part"
        if customer_parts and key == quirks.get("element"):
            price = 0.0
        lines.append(dict(description=name, category=cat, quantity=q, unit_price=price,
                          line_total=round(q * price, 2)))
    subtotal = round(sum(line["line_total"] for line in lines), 2)
    vat = quirks.get("vat")
    total = round(subtotal * (1 + vat / 100), 2) if vat else subtotal

    stated_travel = travel
    if quirks.get("travel_copy_bug"):
        # Technician copy-pasted on-site hours into the travel field (real data entry error).
        stated_travel = onsite
        lines[0]["quantity"] = onsite
        lines[0]["line_total"] = round(onsite * trav_rate, 2)
        subtotal = round(sum(line["line_total"] for line in lines), 2)
        total = subtotal

    if style == "formal":
        cost_status = "truncated" if quirks.get("truncate") else "itemized"
    elif style == "prose":
        cost_status = "total_only"
        total = round(total, -1) if cur in ("USD", "EUR", "GBP", "AUD") else round(total, -2)
    else:
        cost_status = "not_stated"

    date_txt, ambiguous = fmt_date(d, style)
    finding = rng.choice(FINDINGS[task])
    qty_txt = f"{qty} {unit_label}" if qty and unit_label else ""
    equipment_name = {
        "ClarionRO": f"ClarionRO {model} reverse osmosis skid",
        "HPX": f"HPX high-pressure pump ({model}) on RO feed",
        "UltraGuard": f"UltraGuard {model} ultrafiltration system",
        "LumenUV": f"LumenUV {model} UV reactor",
        "DosePro": f"DosePro {model} metering pumps (antiscalant dosing)",
        "HydraBed": f"{model} multimedia filters",
        "SoftLine": f"SoftLine {model} softener",
        "AquaTune": "AquaTune Optimizer (RO control software)",
        "CarbonGuard": f"{model} activated carbon filters",
    }[fam]

    if style == "formal":
        text = write_formal(rid, site, d, date_txt, tech, equipment_name, task_type, qty_txt, onsite,
                            stated_travel, lines, cur, finding, total, vat, subtotal, weekend,
                            customer_parts, quirks.get("truncate"))
    elif style == "prose":
        text = write_prose(rid, site, date_txt, tech, equipment_name, task, qty_txt, onsite, stated_travel,
                           cur, total, finding, quirks.get("dollar_only"))
    elif style == "shorthand":
        text = write_shorthand(rid, site, date_txt, tech, equipment_name, task, qty_txt, onsite,
                               stated_travel, finding, weekend)
    else:
        text = write_minimal(rid, site, d, tech, equipment_name, task, qty_txt, onsite, stated_travel)

    truth = dict(
        report_id=rid, visit_date=d.isoformat(), date_ambiguous=ambiguous, customer=site["customer"],
        site=site["site"], country=site["country"], equipment_family=fam, equipment_model=model,
        task_type=task_type, main_quantity=qty, onsite_hours=onsite, travel_hours=stated_travel,
        currency=cur, labour_rate=lab_rate if style == "formal" else None,
        travel_rate=trav_rate if style == "formal" else None, weekend_or_overtime=weekend,
        parts_supplied_by_customer=customer_parts, vat_rate_pct=vat,
        cost_status=cost_status,
        stated_total=None if cost_status in ("not_stated", "truncated") else total,
        true_total_ex_vat=subtotal if style == "formal" else None,
        # Only the cost lines a reader can actually see in the report.
        line_items=(lines[:3] if quirks.get("truncate") else lines) if style == "formal" else [],
        data_entry_error="travel_hours copied from onsite_hours" if quirks.get("travel_copy_bug") else None,
    )
    return text, truth


def write_formal(rid, site, d, date_txt, tech, equipment, task_type, qty_txt, onsite, travel, lines, cur,
                 finding, total, vat, subtotal, weekend, customer_parts, truncate) -> str:
    task_title = task_type.replace("_", " ").title()
    out = [
        "**Field Service Report**  ",
        f"**Report Reference:** {rid}",
        "",
        "---",
        "",
        f"**Customer:** {site['customer']}  ",
        f"**Site Location:** {site['site']}  ",
        f"**Visit Date:** {date_txt}{' (Saturday call-out)' if weekend else ''}  ",
        f"**Technician:** {tech}  ",
        "",
        "### Equipment Serviced",
        f"- {equipment}",
        "",
        "### Scope",
        f"- {task_title}{' — ' + qty_txt if qty_txt else ''}",
        "",
        "### Time",
        "",
        "| Item | Hours |",
        "|------|-------|",
        f"| On-site work | {onsite} |",
        f"| Travel (round trip) | {travel} |",
        "",
        "### Materials and Parts Used",
        "",
        "| Item | Qty | Notes |",
        "|------|-----|-------|",
    ]
    for line in lines[2:]:
        note = "Customer stock — no charge" if customer_parts and line["unit_price"] == 0 else ""
        out.append(f"| {line['description']} | {line['quantity']:g} | {note} |")
    out += ["", "### Findings", f"- {finding}", "- Equipment returned to service and test run completed.", "",
            "### Cost Summary", "", f"| Description | Qty | Rate ({cur}) | Total ({cur}) |",
            "|-------------|-----|------------|-------------|"]
    shown = lines[:3] if truncate else lines
    for line in shown:
        out.append(f"| {line['description']} | {line['quantity']:g} | {money(line['unit_price'], cur)} | "
                   f"{money(line['line_total'], cur)} |")
    if truncate:
        out.append(f"| {lines[3]['description'] if len(lines) > 3 else 'PPE'} | ")
        return "\n".join(out)
    if vat:
        out += [f"| **Subtotal** | | | **{money(subtotal, cur)}** |",
                f"| VAT ({vat}%) | | | {money(total - subtotal, cur)} |",
                f"| **Total incl. VAT** | | | **{money(total, cur)}** |"]
    else:
        out.append(f"| **Total** | | | **{money(total, cur)}** |")
    out += ["", f"**Technician Signature:** {tech}  ", f"**Date:** {d.isoformat()}"]
    return "\n".join(out)


def write_prose(rid, site, date_txt, tech, equipment, task, qty_txt, onsite, travel, cur, total, finding,
                dollar_only) -> str:
    action = {
        "ro_membranes": f"replaced {qty_txt} and renewed the interconnector O-rings",
        "ro_cip": f"ran a two-step clean-in-place (acid then alkaline) on {qty_txt}",
        "ro_cal": "recalibrated the conductivity, flow and pressure instruments",
        "ro_emergency": "isolated the train, replaced the failed seals and restarted the unit",
        "hpx_seal": "replaced the mechanical seal cartridge and checked alignment",
        "uf_modules": f"replaced {qty_txt} and re-ran the integrity test",
        "uf_integrity": "ran the pressure decay test on all racks and isolated one module",
        "uv_lamps": f"replaced {qty_txt} and cleaned the quartz sleeves",
        "dose_dia": f"rebuilt {qty_txt} with new diaphragm kits and re-verified dosing rates",
        "mmf_media": f"removed and replaced the media in {qty_txt}",
        "soft_resin": f"replaced the resin in {qty_txt} and rebuilt the control valve",
        "tune_comm": "commissioned the AquaTune Optimizer and trained the operators",
    }[task]
    cur_txt = "$" if dollar_only else f"{cur} "
    return "\n".join([
        "Field Service Report",
        f"Reference: {rid}",
        "",
        f"Customer: {site['customer']}",
        f"Site: {site['site']}",
        f"Date of Visit: {date_txt}",
        f"Technician: {tech}",
        f"Equipment: {equipment}",
        "",
        "Summary of Work:",
        f"Arrived on site and met the plant team. {finding} I {action}. "
        "Ran the system for a while afterwards and confirmed readings were back within the normal range. "
        "Briefed the operators before leaving.",
        "",
        f"Total time on site was about {onsite} hours, plus {travel} hours of travel.",
        f"Total cost for this visit (labour, travel and parts): {cur_txt}{money(total, 'USD' if dollar_only else cur)}",
        "",
        "Please get in touch if anything comes up.",
    ])


def write_shorthand(rid, site, date_txt, tech, equipment, task, qty_txt, onsite, travel, finding,
                    weekend) -> str:
    did = {
        "ro_membranes": f"swapped {qty_txt}, new o-rings on interconnectors, flushed to drain 30 min",
        "ro_cip": f"CIP {qty_txt}, acid then high pH, rinse, back online",
        "ro_emergency": "brine leak on vessel end cap - changed o-rings, retorqued, ok",
        "ro_inspection": "walkdown, checked vessels / frame / valves, sample valve leaking - tightened",
        "hpx_seal": "changed mech seal, realigned coupling, no leaks after",
        "uf_integrity": "PDT on all racks, 1 module isolated, pins in",
        "uv_lamps": f"changed {qty_txt}, wiped sleeves, reset hour counter",
        "mmf_media": f"dug out media {qty_txt}, refilled garnet/sand/anthracite, backwashed x2",
        "soft_resin": f"resin swap {qty_txt}, valve looked ok",
        "acf_media": f"carbon change {qty_txt}",
    }[task]
    return "\n".join([
        rid,
        f"Cust: {site['customer']}",
        f"Site: {site['site'].split(',')[0]}",
        f"Date: {date_txt}{' (Sat)' if weekend else ''}",
        f"Tech: {tech.split()[0][0]}. {tech.split()[1]}",
        f"Equip: {equipment}",
        "",
        f"Arr. site ~7:40, toolbox talk + permit took a while. {finding.lower()}",
        f"Work: {did}.",
        "Cal sensors ok. Forgot to log exact start/finish, sorry.",
        "",
        f"Time: abt {onsite}h on site, travel {travel}h total.",
        "Cost: as per agreement + travel (see last PO).",
        "",
        f"-{tech.split()[0]}",
    ])


def write_minimal(rid, site, d, tech, equipment, task, qty_txt, onsite, travel) -> str:
    return "\n".join([
        rid,
        f"Customer: {site['customer']}",
        f"Location: {site['site']}",
        f"Date: {d.isoformat()}",
        f"Technician: {tech}",
        "",
        f"Carried out standard maintenance on {equipment}. "
        f"{('Replaced ' + qty_txt + '. ') if qty_txt else ''}Checked equipment, replaced worn parts as needed. "
        f"Total on-site hours: {onsite}h. Travel: {travel}h. Invoice to follow.",
    ])


# ---------------------------------------------------------------------------
# Process data: hourly log from one brackish-water ClarionRO-500 plant
# ---------------------------------------------------------------------------

def generate_process_data() -> pd.DataFrame:
    g = np.random.default_rng(7)
    idx = pd.date_range("2024-01-01 00:00", "2024-06-30 23:00", freq="h")
    n = len(idx)
    t = np.arange(n)
    day = t / 24.0

    # Feed temperature: seasonal warming plus a daily cycle.
    temp = 15 + 10 * (day / day[-1]) + 1.0 * np.sin(2 * np.pi * (t % 24) / 24) + g.normal(0, 0.4, n)

    # Rain events: turbidity spikes, conductivity dips (river-influenced wellfield).
    turb = 0.35 + g.gamma(2.0, 0.08, n)
    cond_dip = np.zeros(n)
    for start in g.choice(n - 200, 11, replace=False):
        peak = g.uniform(6, 35)
        length = int(g.integers(30, 110))
        decay = peak * np.exp(-np.arange(length) / (length / 4))
        turb[start:start + length] += decay
        cond_dip[start:start + length] += decay / peak * g.uniform(300, 700)
    walk = np.cumsum(g.normal(0, 6, n))
    feed_cond = 2450 + walk - walk.mean() - cond_dip + g.normal(0, 25, n)

    # Recovery setpoint changed by operators a few times.
    recovery = np.full(n, 75.0)
    recovery[(day > 40) & (day < 62)] = 78.0
    recovery[(day > 120) & (day < 150)] = 72.0
    recovery += g.normal(0, 0.4, n)

    # Antiscalant: dosing pump degraded (diaphragm) from day 95 to 108.
    dose = np.full(n, 3.0) + g.normal(0, 0.08, n)
    dose[(day > 95) & (day < 108)] = np.linspace(2.6, 1.6, ((day > 95) & (day < 108)).sum())

    # Fouling state: grows with time and turbidity exposure; reset by CIP.
    cip_days = [33, 74, 112, 150]
    fouling = np.zeros(n)
    f = 0.05
    scaling = 0.0
    for k in range(n):
        f += 0.00018 + 0.00006 * max(turb[k] - 1.0, 0)
        if dose[k] < 2.4 and recovery[k] > 74:
            scaling += 0.0012
        else:
            scaling = max(scaling - 0.0002, 0)
        if any(abs(day[k] - c) < 1 / 24 for c in cip_days):
            f = 0.06
            scaling = 0.0
        fouling[k] = f + scaling

    r = recovery / 100
    conc_factor = np.log(1 / (1 - r)) / r
    salt_passage = (0.0105 * np.exp(0.03 * (temp - 25)) * (1 + 2.0 * (r - 0.75))
                    * (1 + 1.1 * fouling) * np.exp(g.normal(0, 0.05, n)))
    perm_cond = feed_cond * conc_factor * salt_passage

    perm_flow = 20.8 * (1 - 0.25 * fouling) + g.normal(0, 0.15, n)
    feed_flow = perm_flow / r
    feed_pressure = 11.2 + 0.12 * (25 - temp) + 3.2 * fouling + 0.08 * (recovery - 75) + g.normal(0, 0.15, n)
    dp = 1.3 + 1.9 * fouling + g.normal(0, 0.05, n)
    ph = 7.3 + g.normal(0, 0.12, n)

    # SDI is a manual lab test every 8 hours; value is carried forward between tests.
    sdi_raw = 2.4 + 0.12 * np.clip(turb - 0.5, 0, None) + g.normal(0, 0.2, n)
    sdi = pd.Series(sdi_raw).where(t % 8 == 0).ffill().to_numpy()

    df = pd.DataFrame({
        "timestamp": idx,
        "feed_temperature_C": temp.round(1),
        "feed_conductivity_uS_cm": feed_cond.round(0),
        "feed_turbidity_NTU": turb.round(2),
        "feed_pH": ph.round(2),
        "feed_SDI15": sdi.round(1),
        "antiscalant_dose_mg_L": dose.round(2),
        "feed_pressure_bar": feed_pressure.round(2),
        "differential_pressure_bar": dp.round(2),
        "feed_flow_m3h": feed_flow.round(2),
        "permeate_flow_m3h": perm_flow.round(2),
        "recovery_pct": recovery.round(1),
        "permeate_conductivity_uS_cm": perm_cond.round(1),
    })

    # Plant offline during each CIP (~10 h): flows read zero, other readings meaningless.
    for c in cip_days:
        off = (day >= c - 0.2) & (day < c + 0.25)
        df.loc[off, ["feed_flow_m3h", "permeate_flow_m3h"]] = 0.0
        df.loc[off, "recovery_pct"] = 0.0
        df.loc[off, "permeate_conductivity_uS_cm"] = df.loc[off, "permeate_conductivity_uS_cm"] * 3
    # A stuck permeate conductivity sensor for 36 hours, and a few dropped rows.
    stuck = (day > 128) & (day < 129.5)
    df.loc[stuck, "permeate_conductivity_uS_cm"] = df.loc[stuck, "permeate_conductivity_uS_cm"].iloc[0]
    for col in ("feed_turbidity_NTU", "permeate_conductivity_uS_cm", "feed_pressure_bar"):
        df.loc[g.choice(n, 15, replace=False), col] = np.nan
    return df


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    PROCESS_DIR.mkdir(parents=True, exist_ok=True)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    jobs = list(JOBS)
    rng.shuffle(jobs)  # so report numbers don't group by equipment
    truths = []
    for i, (job, d) in enumerate(zip(jobs, job_dates()), start=1):
        text, truth = build_job(i, job, d)
        (REPORT_DIR / f"{truth['report_id']}.txt").write_text(text + "\n", encoding="utf-8")
        truths.append(truth)
    (EVAL_DIR / "ground_truth_service_records.json").write_text(json.dumps(truths, indent=2), encoding="utf-8")

    df = generate_process_data()
    df.to_csv(PROCESS_DIR / "ro_plant_hourly.csv", index=False)
    print(f"Wrote {len(truths)} service reports and {len(df)} hourly process rows.")


if __name__ == "__main__":
    main()

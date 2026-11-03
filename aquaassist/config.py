"""Paths, model settings and business assumptions in one place."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
BROCHURE_DIR = DATA_DIR / "brochures"
REPORT_DIR = DATA_DIR / "service_reports"
PROCESS_CSV = DATA_DIR / "process" / "ro_plant_hourly.csv"
INDEX_DIR = ROOT / "index"
RECORDS_PATH = INDEX_DIR / "service_records.json"

MODEL = os.environ.get("AQUAASSIST_MODEL", "claude-opus-5-5")
# Opus 5.5 always thinks; effort is the only depth control (its default is "medium").
AGENT_EFFORT = os.environ.get("AQUAASSIST_EFFORT", "medium")
EXTRACTION_EFFORT = os.environ.get("AQUAASSIST_EXTRACTION_EFFORT", "low")
MAX_AGENT_STEPS = 10


def llm_credentials_configured() -> bool:
    """Whether an Anthropic API key is set. Only presence is checked; the value is never read out."""
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

# Fixed exchange rates (USD per 1 unit). A deliberate, visible assumption: the
# assistant reports these alongside any converted figure instead of hiding them.
FX_TO_USD = {
    "USD": 1.0, "EUR": 1.08, "GBP": 1.27, "SAR": 0.2667, "AED": 0.2723,
    "AUD": 0.66, "INR": 0.012, "ZAR": 0.054, "MXN": 0.054,
}
FX_NOTE = "Fixed planning rates (USD per unit): " + ", ".join(f"{k} {v}" for k, v in FX_TO_USD.items())

EQUIPMENT_FAMILIES = [
    "ClarionRO", "PureMem", "UltraGuard", "LumenUV", "DosePro", "HydraBed",
    "CarbonGuard", "SoftLine", "HPX", "AquaTune", "other",
]
TASK_TYPES = [
    "membrane_replacement", "cip_cleaning", "sensor_calibration", "emergency_repair", "inspection",
    "pump_seal_replacement", "pump_overhaul", "uf_module_replacement", "integrity_test",
    "uv_lamp_replacement", "diaphragm_replacement", "media_replacement", "resin_replacement",
    "commissioning", "software_update", "other",
]

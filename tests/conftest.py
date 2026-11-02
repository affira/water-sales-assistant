import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aquaassist.extraction import validate  # noqa: E402


@pytest.fixture(scope="session")
def records():
    """Ground-truth records in the same shape the extractor produces, so tools run without an API key."""
    truth = json.loads((ROOT / "eval" / "ground_truth_service_records.json").read_text())
    for r in truth:
        r["checks"] = validate(r)
    return truth

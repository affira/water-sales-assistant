import json
import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Importing aquaassist.api runs start-up; real Key Vault / monitoring settings from the shell
# must never reach tests (tests that need them set them explicitly with monkeypatch).
for _var in ("KEY_VAULT_URL", "APPLICATIONINSIGHTS_CONNECTION_STRING"):
    os.environ.pop(_var, None)

from aquaassist.extraction import validate  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never reach a real API: any outbound connection fails loudly."""
    def refuse(*args, **kwargs):
        raise RuntimeError("Network access is not allowed in tests; use the fakes in tests/fakes.py")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(scope="session")
def records():
    """Ground-truth records in the same shape the extractor produces, so tools run without an API key."""
    truth = json.loads((ROOT / "eval" / "ground_truth_service_records.json").read_text())
    for r in truth:
        r["checks"] = validate(r)
    return truth

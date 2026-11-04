"""Process start-up shared by the API, CLI and ingest."""
from __future__ import annotations

from . import secret_store, telemetry


def bootstrap() -> None:
    """Load API keys (Key Vault or environment), then switch on monitoring if configured."""
    secret_store.load_secrets()
    telemetry.setup_telemetry()

"""Load API keys from Azure Key Vault when KEY_VAULT_URL is set, else use environment variables.

Authentication uses DefaultAzureCredential: the managed identity on Azure, `az login` on a laptop.
Key Vault secret names cannot contain underscores, so each variable maps to a lower-case,
hyphenated name: ANTHROPIC_API_KEY -> anthropic-api-key.

Values found in Key Vault are placed into this process's environment so the rest of the app
reads configuration one way. Only variable *names* and their source are ever logged.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Optional

from azure.core.exceptions import ResourceNotFoundError

logger = logging.getLogger("aquaassist.secrets")

KEY_VAULT_URL_VAR = "KEY_VAULT_URL"
SECRET_VARS = ("ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "ANTHROPIC_FOUNDRY_API_KEY")

SecretClientFactory = Callable[[str], Any]

_last_sources: Dict[str, str] = {}


def secret_name(var: str) -> str:
    return var.lower().replace("_", "-")


def key_vault_enabled() -> bool:
    return bool(os.environ.get(KEY_VAULT_URL_VAR, "").strip())


def _default_client(vault_url: str) -> Any:
    from azure.identity import DefaultAzureCredential  # imported only when Key Vault is used
    from azure.keyvault.secrets import SecretClient
    return SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())


def _environment_sources() -> Dict[str, str]:
    return {var: "environment" for var in SECRET_VARS if os.environ.get(var, "").strip()}


class _VaultUnavailable(Exception):
    """The vault could not be reached or refused access, so further reads would fail the same way."""


def _read_secret(client: Any, var: str) -> Optional[str]:
    try:
        return client.get_secret(secret_name(var)).value
    except ResourceNotFoundError:
        logger.info("%s not found in Key Vault; using the environment variable if set", var)
        return None
    except Exception as exc:  # auth/network problems: report the type only, never details
        logger.warning("Key Vault unavailable (%s); using environment variables", type(exc).__name__)
        raise _VaultUnavailable() from None


def load_secrets(client_factory: Optional[SecretClientFactory] = None) -> Dict[str, str]:
    """Load keys and return {variable name: "key_vault" | "environment"}. Never returns values."""
    global _last_sources
    vault_url = os.environ.get(KEY_VAULT_URL_VAR, "").strip()
    if not vault_url:
        _last_sources = _environment_sources()
        return dict(_last_sources)
    try:
        client = (client_factory or _default_client)(vault_url)
    except Exception as exc:
        logger.warning("Key Vault client could not be created (%s); using environment variables",
                       type(exc).__name__)
        _last_sources = _environment_sources()
        return dict(_last_sources)

    sources = _environment_sources()
    for var in SECRET_VARS:
        try:
            value = _read_secret(client, var)
        except _VaultUnavailable:
            break  # don't wait on the same failure for every secret
        if value:
            os.environ[var] = value
            sources[var] = "key_vault"
    logger.info("API keys loaded (names and sources only): %s", sources)
    _last_sources = sources
    return dict(sources)


def last_sources() -> Dict[str, str]:
    return dict(_last_sources)

"""Key Vault secrets with a fake SecretClient (offline)."""
from __future__ import annotations

import logging
import os
from types import SimpleNamespace as NS
from typing import Any, Dict, Iterator, List, Optional

import azure.identity
import azure.keyvault.secrets
import pytest
from azure.core.exceptions import ClientAuthenticationError, ResourceNotFoundError

from aquaassist import providers, runtime, secret_store, telemetry
from aquaassist.secret_store import SECRET_VARS, load_secrets, secret_name

VAULT_URL = "https://norvale-test.vault.azure.net/"
VAULT_VALUE = "kv-SUPERSECRET-value-123"
ENV_VALUE = "env-SUPERSECRET-value-456"


class FakeSecretClient:
    def __init__(self, secrets: Dict[str, str], error: Optional[Exception] = None):
        self.secrets, self.error = secrets, error
        self.requested: List[str] = []

    def get_secret(self, name: str) -> NS:
        self.requested.append(name)
        if self.error:
            raise self.error
        if name not in self.secrets:
            raise ResourceNotFoundError("SecretNotFound")
        return NS(value=self.secrets[name])


@pytest.fixture(autouse=True)
def clean_secret_env() -> Iterator[None]:
    """The code writes vault values into os.environ; restore it exactly after each test."""
    saved = {var: os.environ.get(var) for var in (*SECRET_VARS, secret_store.KEY_VAULT_URL_VAR)}
    for var in saved:
        os.environ.pop(var, None)
    yield
    for var, value in saved.items():
        os.environ.pop(var, None)
        if value is not None:
            os.environ[var] = value


def factory_for(client: FakeSecretClient, seen_urls: Optional[List[str]] = None):
    def make(url: str) -> FakeSecretClient:
        if seen_urls is not None:
            seen_urls.append(url)
        return client
    return make


def test_secret_names_use_hyphens() -> None:
    assert secret_name("AZURE_OPENAI_API_KEY") == "azure-openai-api-key"


def test_without_vault_url_environment_is_used_and_no_client_is_created() -> None:
    os.environ["ANTHROPIC_API_KEY"] = ENV_VALUE

    def must_not_be_called(url: str) -> Any:
        raise AssertionError("Key Vault client created without KEY_VAULT_URL")

    assert load_secrets(must_not_be_called) == {"ANTHROPIC_API_KEY": "environment"}
    assert os.environ["ANTHROPIC_API_KEY"] == ENV_VALUE


def test_vault_values_win_over_environment() -> None:
    os.environ[secret_store.KEY_VAULT_URL_VAR] = VAULT_URL
    os.environ["AZURE_OPENAI_API_KEY"] = ENV_VALUE
    client = FakeSecretClient({"azure-openai-api-key": VAULT_VALUE})
    urls: List[str] = []

    sources = load_secrets(factory_for(client, urls))

    assert urls == [VAULT_URL]
    assert client.requested == [secret_name(v) for v in SECRET_VARS]
    assert os.environ["AZURE_OPENAI_API_KEY"] == VAULT_VALUE
    assert sources == {"AZURE_OPENAI_API_KEY": "key_vault"}


def test_secret_missing_from_vault_falls_back_to_environment() -> None:
    os.environ[secret_store.KEY_VAULT_URL_VAR] = VAULT_URL
    os.environ["ANTHROPIC_API_KEY"] = ENV_VALUE
    sources = load_secrets(factory_for(FakeSecretClient({})))
    assert os.environ["ANTHROPIC_API_KEY"] == ENV_VALUE
    assert sources == {"ANTHROPIC_API_KEY": "environment"}


def test_vault_access_failure_falls_back_to_environment(caplog: pytest.LogCaptureFixture) -> None:
    os.environ[secret_store.KEY_VAULT_URL_VAR] = VAULT_URL
    os.environ["ANTHROPIC_API_KEY"] = ENV_VALUE
    client = FakeSecretClient({}, error=ClientAuthenticationError(f"denied; token={VAULT_VALUE}"))
    with caplog.at_level(logging.DEBUG, logger="aquaassist"):
        sources = load_secrets(factory_for(client))
    assert sources == {"ANTHROPIC_API_KEY": "environment"}
    assert client.requested == ["anthropic-api-key"]  # stops after the first failure instead of retrying each
    assert "ClientAuthenticationError" in caplog.text
    assert VAULT_VALUE not in caplog.text  # exception details are never logged


def test_client_creation_failure_falls_back_to_environment() -> None:
    os.environ[secret_store.KEY_VAULT_URL_VAR] = VAULT_URL
    os.environ["ANTHROPIC_API_KEY"] = ENV_VALUE

    def broken(url: str) -> Any:
        raise RuntimeError("no credential available")

    assert load_secrets(broken) == {"ANTHROPIC_API_KEY": "environment"}


def test_secret_values_are_never_logged_or_returned(caplog: pytest.LogCaptureFixture) -> None:
    os.environ[secret_store.KEY_VAULT_URL_VAR] = VAULT_URL
    os.environ["ANTHROPIC_FOUNDRY_API_KEY"] = ENV_VALUE
    client = FakeSecretClient({secret_name(v): VAULT_VALUE for v in SECRET_VARS})
    with caplog.at_level(logging.DEBUG):
        sources = load_secrets(factory_for(client))
    assert set(sources.values()) == {"key_vault"}
    for value in (VAULT_VALUE, ENV_VALUE):
        assert value not in caplog.text and value not in str(sources)


def test_default_client_uses_default_azure_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Managed identity on Azure / az login locally: both come from DefaultAzureCredential."""
    created: Dict[str, Any] = {}

    class FakeCredential:
        pass

    def fake_secret_client(vault_url: str, credential: Any) -> FakeSecretClient:
        created.update(vault_url=vault_url, credential=credential)
        return FakeSecretClient({"anthropic-api-key": VAULT_VALUE})

    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", FakeCredential)
    monkeypatch.setattr(azure.keyvault.secrets, "SecretClient", fake_secret_client)
    os.environ[secret_store.KEY_VAULT_URL_VAR] = VAULT_URL

    assert load_secrets() == {"ANTHROPIC_API_KEY": "key_vault"}
    assert created["vault_url"] == VAULT_URL and isinstance(created["credential"], FakeCredential)


def test_vault_keys_satisfy_provider_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert providers.configuration_problems() == ["ANTHROPIC_API_KEY is not set"]
    os.environ[secret_store.KEY_VAULT_URL_VAR] = VAULT_URL
    load_secrets(factory_for(FakeSecretClient({"anthropic-api-key": VAULT_VALUE})))
    assert providers.configuration_problems() == []


def test_bootstrap_loads_secrets_then_monitoring(monkeypatch: pytest.MonkeyPatch) -> None:
    order: List[str] = []
    monkeypatch.setattr(secret_store, "load_secrets", lambda: order.append("secrets"))
    monkeypatch.setattr(telemetry, "setup_telemetry", lambda: order.append("telemetry"))
    runtime.bootstrap()
    assert order == ["secrets", "telemetry"]

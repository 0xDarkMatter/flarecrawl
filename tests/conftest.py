"""Test fixtures for Flarecrawl."""

import pytest


@pytest.fixture
def mock_credentials(monkeypatch):
    """Set fake credentials via env vars."""
    monkeypatch.setenv("FLARECRAWL_ACCOUNT_ID", "test-account-id")
    monkeypatch.setenv("FLARECRAWL_API_TOKEN", "test-api-token")


@pytest.fixture
def no_credentials(monkeypatch, tmp_path):
    """Ensure no credentials are available (env vars, keyring, .env, legacy config)."""
    monkeypatch.delenv("FLARECRAWL_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
    # Block legacy config.json and keyring
    monkeypatch.setattr("flarecrawl.config.load_config", lambda: {})
    monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
    monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: tmp_path / "nonexistent.json")
    # Reset singleton so fresh store is created
    import flarecrawl.credentials as _creds
    monkeypatch.setattr(_creds, "_store", None)

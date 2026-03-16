"""Test fixtures for Flarecrawl."""

import pytest


@pytest.fixture
def mock_credentials(monkeypatch):
    """Set fake credentials via env vars."""
    monkeypatch.setenv("FLARECRAWL_ACCOUNT_ID", "test-account-id")
    monkeypatch.setenv("FLARECRAWL_API_TOKEN", "test-api-token")


@pytest.fixture
def no_credentials(monkeypatch):
    """Ensure no credentials are available (env vars AND config file)."""
    monkeypatch.delenv("FLARECRAWL_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
    # Also block the config file from returning stored creds
    monkeypatch.setattr("flarecrawl.config.load_config", lambda: {})

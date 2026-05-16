"""Test fixtures for Flarecrawl."""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Routing fixture server
# ---------------------------------------------------------------------------

_ROUTING_FIXTURES = Path(__file__).parent / "fixtures" / "routing"

# Explicit Content-Type map — overrides Python's mimetypes for types it
# doesn't know about (.kml, .atom, .rss, .ttl, .ndjson, .jsonld, .vcf …)
_CONTENT_TYPES: dict[str, str] = {
    ".xml":     "application/xml",
    ".csv":     "text/csv; charset=utf-8",
    ".tsv":     "text/tab-separated-values; charset=utf-8",
    ".rss":     "application/rss+xml; charset=utf-8",
    ".atom":    "application/atom+xml; charset=utf-8",
    ".kml":     "application/vnd.google-earth.kml+xml; charset=utf-8",
    ".yaml":    "application/yaml; charset=utf-8",
    ".yml":     "application/yaml; charset=utf-8",
    ".toml":    "application/toml; charset=utf-8",
    ".json":    "application/json; charset=utf-8",
    ".ndjson":  "application/x-ndjson; charset=utf-8",
    ".jsonl":   "application/x-jsonlines; charset=utf-8",
    ".geojson": "application/geo+json; charset=utf-8",
    ".jsonld":  "application/ld+json; charset=utf-8",
    ".ics":     "text/calendar; charset=utf-8",
    ".vcf":     "text/vcard; charset=utf-8",
    ".ttl":     "text/turtle; charset=utf-8",
    ".md":      "text/markdown; charset=utf-8",
    ".txt":     "text/plain; charset=utf-8",
    ".html":    "text/html; charset=utf-8",
    ".htm":     "text/html; charset=utf-8",
}


class _RoutingHandler(http.server.SimpleHTTPRequestHandler):
    """Serves tests/fixtures/routing/ with explicit Content-Type headers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_ROUTING_FIXTURES), **kwargs)

    def guess_type(self, path):  # type: ignore[override]
        ext = Path(path).suffix.lower()
        return _CONTENT_TYPES.get(ext, "application/octet-stream")

    def log_message(self, format, *args):  # noqa: A002
        pass  # suppress request log noise in test output


@pytest.fixture(scope="session")
def routing_server():
    """Session-scoped local HTTP server serving tests/fixtures/routing/.

    Yields the base URL, e.g. 'http://localhost:49152'.
    """
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RoutingHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


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

"""Live test configuration — local HTTP server and markers."""

from __future__ import annotations

import http.server
import os
import threading
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
LOCAL_PORT = 8787
LOCAL_BASE = f"http://localhost:{LOCAL_PORT}"


def pytest_configure(config):
    config.addinivalue_line("markers", "live: live tests against real endpoints")
    config.addinivalue_line("markers", "cdp: requires CDP/WebSocket (CF auth needed)")
    config.addinivalue_line("markers", "local: uses local HTTP server only")


class _FixtureHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES_DIR), **kwargs)

    def log_message(self, format, *args):
        pass  # suppress request logging


@pytest.fixture(scope="session")
def local_server():
    """Start a local HTTP server serving test fixtures."""
    server = http.server.HTTPServer(("127.0.0.1", LOCAL_PORT), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield LOCAL_BASE
    server.shutdown()


@pytest.fixture
def has_cf_auth():
    """Check if Cloudflare credentials are configured."""
    from flarecrawl.config import get_account_id, get_api_token
    account_id = get_account_id()
    api_token = get_api_token()
    if not account_id or not api_token:
        pytest.skip("No CF auth — set FLARECRAWL_ACCOUNT_ID + FLARECRAWL_API_TOKEN")
    return True

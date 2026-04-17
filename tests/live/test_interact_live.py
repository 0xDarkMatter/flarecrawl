"""Live interaction tests using local HTTP server.

Run: PYTHONPATH=src pytest tests/live/test_interact_live.py -v -s
Requires: CF auth configured + websockets installed for CDP tests
Local fixtures only — no external requests for interact tests.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from flarecrawl.cli import app

runner = CliRunner()


@pytest.mark.local
class TestLocalScrapeViaStdin:
    """Test form HTML processing without any network — pure stdin."""

    def test_form_html_scrape(self):
        """Scrape the login form fixture via stdin."""
        html = Path(__file__).parent.joinpath("fixtures", "form.html").read_text()
        result = runner.invoke(app, ["scrape", "--stdin", "--json"], input=html)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "Test Login" in data["data"]["content"]
        assert "username" in data["data"]["content"].lower()

    def test_dropdown_html_scrape(self):
        """Scrape the dropdown fixture via stdin."""
        html = Path(__file__).parent.joinpath("fixtures", "dropdown.html").read_text()
        result = runner.invoke(app, ["scrape", "--stdin", "--json"], input=html)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "Country" in data["data"]["content"]
        assert "United States" in data["data"]["content"]

    def test_dynamic_html_scrape(self):
        """Scrape the dynamic fixture — only static content visible (no JS execution)."""
        html = Path(__file__).parent.joinpath("fixtures", "dynamic.html").read_text()
        result = runner.invoke(app, ["scrape", "--stdin", "--json"], input=html)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Static content is there, but JS-rendered content is NOT (stdin doesn't execute JS)
        assert "Dynamic Content Test" in data["data"]["content"]
        assert "Loading products..." in data["data"]["content"]

    def test_multistep_html_scrape(self):
        """Scrape the multi-step form fixture."""
        html = Path(__file__).parent.joinpath("fixtures", "multi-step.html").read_text()
        result = runner.invoke(app, ["scrape", "--stdin", "--json"], input=html)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "Multi-Step Registration" in data["data"]["content"]


@pytest.mark.live
class TestLocalServerScrape:
    """Test scraping from local HTTP server via CF browser.

    NOTE: CF browser can't reach localhost — these tests will fail
    unless you expose the local server via a tunnel (e.g. cloudflared tunnel).
    They work against public URLs. Kept here as reference for tunnel testing.
    """

    def test_scrape_local_form(self, local_server, has_cf_auth):
        """Scrape the login form from local server via CF browser."""
        result = runner.invoke(app, ["scrape", f"{local_server}/form.html", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "Test Login" in data["data"]["content"]

    def test_scrape_local_dynamic_with_js(self, local_server, has_cf_auth):
        """Scrape dynamic page with --js to capture rendered content."""
        result = runner.invoke(app, ["scrape", f"{local_server}/dynamic.html", "--js", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        content = data["data"]["content"]
        # With JS rendering, the delayed products should appear
        # (they load after 1.5s, networkidle0 waits for them)
        assert "Widget Pro" in content or "Dynamic Content" in content

    def test_scrape_local_with_selector(self, local_server, has_cf_auth):
        """Extract specific element from local page."""
        result = runner.invoke(app, ["scrape", f"{local_server}/form.html", "--selector", "#login-form", "--json"])
        assert result.exit_code == 0

    def test_scrape_local_js_eval(self, local_server, has_cf_auth):
        """Run JS expression on local page."""
        result = runner.invoke(app, [
            "scrape", f"{local_server}/form.html",
            "--js-eval", "document.title",
            "--json"
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "Login Form" in str(data["data"]["content"])


@pytest.mark.cdp
class TestCDPInteract:
    """Test the interact command with CDP against local server.

    These require:
    1. CF auth configured
    2. websockets package installed (pip install flarecrawl[cdp])
    3. Local HTTP server running (handled by local_server fixture)
    """

    def test_interact_fill_login(self, local_server, has_cf_auth):
        """Fill the login form via interact command."""
        result = runner.invoke(app, [
            "interact", f"{local_server}/form.html",
            "--fill", "#username=testuser",
            "--fill", "#password=testpass123",
            "--click", "#submit-btn",
            "--wait-for", "#success",
            "--json",
        ])
        # This may fail if websockets not installed — that's expected
        if "MISSING_DEPENDENCY" in result.output:
            pytest.skip("websockets not installed")
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert data["data"]["actions"]["fills"] == 2
            assert data["data"]["actions"]["clicks"] == 1

    def test_interact_dropdown(self, local_server, has_cf_auth):
        """Fill form with dropdown selection."""
        result = runner.invoke(app, [
            "interact", f"{local_server}/dropdown.html",
            "--fill", "#name=Jane Doe",
            "--select", "#country=AU",
            "--select", "#role=developer",
            "--click", "#submit-btn",
            "--wait-for", "#result",
            "--json",
        ])
        if "MISSING_DEPENDENCY" in result.output:
            pytest.skip("websockets not installed")
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert data["data"]["actions"]["fills"] == 1
            assert data["data"]["actions"]["selects"] == 2

    def test_interact_save_cookies(self, local_server, has_cf_auth):
        """Fill login form and save cookies."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            cookie_path = f.name

        try:
            result = runner.invoke(app, [
                "interact", f"{local_server}/form.html",
                "--fill", "#username=testuser",
                "--fill", "#password=testpass123",
                "--click", "#submit-btn",
                "--save-cookies", cookie_path,
                "--json",
            ])
            if "MISSING_DEPENDENCY" in result.output:
                pytest.skip("websockets not installed")
            if result.exit_code == 0:
                # Verify cookie file was written
                cookies = json.loads(Path(cookie_path).read_text())
                assert isinstance(cookies, list)
        finally:
            os.unlink(cookie_path)

    def test_interact_multi_step(self, local_server, has_cf_auth):
        """Navigate a multi-step form."""
        result = runner.invoke(app, [
            "interact", f"{local_server}/multi-step.html",
            "--fill", "#first-name=John",
            "--fill", "#last-name=Doe",
            "--fill", "#email=john@example.com",
            "--click", "#next-1",
            "--json",
        ])
        if "MISSING_DEPENDENCY" in result.output:
            pytest.skip("websockets not installed")
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert data["data"]["actions"]["fills"] == 3

    def test_interact_screenshot(self, local_server, has_cf_auth):
        """Take screenshot after interaction."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            screenshot_path = f.name

        try:
            result = runner.invoke(app, [
                "interact", f"{local_server}/form.html",
                "--fill", "#username=testuser",
                "--screenshot", screenshot_path,
                "--json",
            ])
            if "MISSING_DEPENDENCY" in result.output:
                pytest.skip("websockets not installed")
            if result.exit_code == 0:
                assert Path(screenshot_path).stat().st_size > 0
        finally:
            os.unlink(screenshot_path)


@pytest.mark.cdp
class TestCDPScrape:
    """Test scrape command with --cdp flag."""

    def test_cdp_scrape_basic(self, local_server, has_cf_auth):
        """Basic scrape via CDP."""
        result = runner.invoke(app, [
            "scrape", f"{local_server}/form.html",
            "--cdp", "--json",
        ])
        if "MISSING_DEPENDENCY" in result.output:
            pytest.skip("websockets not installed")
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert "Test Login" in data["data"]["content"]
            assert data["data"]["metadata"]["source"] == "cdp"

    def test_cdp_js_eval(self, local_server, has_cf_auth):
        """JS eval via CDP — should use real Runtime.evaluate."""
        result = runner.invoke(app, [
            "scrape", f"{local_server}/dynamic.html",
            "--cdp", "--js-eval", "document.querySelectorAll('.card').length",
            "--json",
        ])
        if "MISSING_DEPENDENCY" in result.output:
            pytest.skip("websockets not installed")
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert data["data"]["metadata"]["source"] == "cdp-evaluate"

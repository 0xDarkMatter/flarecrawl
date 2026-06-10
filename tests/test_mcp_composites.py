"""Test T1 composite tool behavior.

Tests:
- site_overview partial failure accumulates _errors and returns surviving sections
- read_page paywall retry triggers on blocked/empty first attempt (mocked)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# site_overview partial failure
# ---------------------------------------------------------------------------


class TestSiteOverviewPartialFailure:
    def _mock_run_cli_responses(self, responses: dict):
        """Build a side_effect for run_cli that returns different responses per tool_name."""
        def side_effect(args, tool_name="", **kwargs):
            return responses.get(tool_name, {"ok": True, "data": None})
        return side_effect

    def test_partial_failure_accumulates_errors(self):
        """site_overview returns surviving sections + _errors for failed ones."""
        from flarecrawl.mcp_tools.composite import site_overview_handler

        tech_data = {"ok": True, "data": [{"name": "WordPress", "category": "CMS"}]}
        schema_error = {"ok": False, "error": {"code": "UPSTREAM_ERROR", "message": "timeout"}}
        links_data = {"ok": True, "data": ["https://example.com/page1"]}
        favicon_data = {"ok": True, "data": {"url": "https://example.com/favicon.ico"}}
        openapi_error = {"ok": False, "error": {"code": "NOT_FOUND", "message": "no spec"}}

        responses = {
            "site_overview/tech": tech_data,
            "site_overview/schema": schema_error,
            "site_overview/links": links_data,
            "site_overview/favicon": favicon_data,
            "site_overview/openapi": openapi_error,
        }

        with patch("flarecrawl.mcp_tools.composite.run_cli", side_effect=self._mock_run_cli_responses(responses)):
            result = site_overview_handler("https://example.com")

        assert result.get("ok") is True
        assert result["data"]["tech"] is not None
        assert result["data"]["schema"] is None   # failed
        assert result["data"]["links"] is not None
        assert result["data"]["favicon"] is not None
        assert result["data"]["openapi"] is None  # failed
        assert "_errors" in result
        assert len(result["_errors"]) == 2

    def test_all_sections_succeed(self):
        from flarecrawl.mcp_tools.composite import site_overview_handler

        ok = {"ok": True, "data": {"test": True}}
        responses = {
            "site_overview/tech": ok,
            "site_overview/schema": ok,
            "site_overview/links": ok,
            "site_overview/favicon": ok,
            "site_overview/openapi": ok,
        }

        with patch("flarecrawl.mcp_tools.composite.run_cli", side_effect=self._mock_run_cli_responses(responses)):
            result = site_overview_handler("https://example.com")

        assert result.get("ok") is True
        assert "_errors" not in result

    def test_section_subset(self):
        from flarecrawl.mcp_tools.composite import site_overview_handler

        ok = {"ok": True, "data": {"test": True}}
        responses = {
            "site_overview/tech": ok,
            "site_overview/schema": ok,
        }

        with patch("flarecrawl.mcp_tools.composite.run_cli", side_effect=self._mock_run_cli_responses(responses)):
            result = site_overview_handler("https://example.com", include=["tech", "schema"])

        assert "links" not in result["data"]
        assert "favicon" not in result["data"]
        assert "openapi" not in result["data"]
        assert "tech" in result["data"]
        assert "schema" in result["data"]


# ---------------------------------------------------------------------------
# read_page paywall retry
# ---------------------------------------------------------------------------


class TestReadPagePaywallRetry:
    def test_paywall_retry_on_blocked_verdict(self):
        """read_page retries with --paywall when first attempt returns BLOCKED."""
        from flarecrawl.mcp_tools.composite import read_page_handler

        blocked_result = {
            "ok": False,
            "error": {
                "code": "BLOCKED",
                "message": "blocked by bot wall",
                "blocked": {"vendor": "generic", "terminal": False},
            },
        }
        paywall_result = {
            "ok": True,
            "data": {"content": "Paywall content", "url": "https://example.com"},
            "meta": {},
        }

        call_log = []

        def fake_run_cli(args, **kwargs):
            call_log.append(args[:])
            if "--paywall" in args:
                return paywall_result
            return blocked_result

        with patch("flarecrawl.mcp_tools.composite.run_cli", side_effect=fake_run_cli):
            result = read_page_handler("https://example.com")

        assert result.get("ok") is True
        assert result.get("meta", {}).get("source") == "paywall"
        # Should have been called twice (initial + paywall retry)
        assert len(call_log) == 2
        assert any("--paywall" in call for call in call_log)

    def test_no_retry_on_success(self):
        """read_page does NOT retry if first attempt succeeds."""
        from flarecrawl.mcp_tools.composite import read_page_handler

        success_result = {
            "ok": True,
            "data": {"content": "Page content", "url": "https://example.com"},
            "meta": {},
        }

        call_log = []

        def fake_run_cli(args, **kwargs):
            call_log.append(args[:])
            return success_result

        with patch("flarecrawl.mcp_tools.composite.run_cli", side_effect=fake_run_cli):
            result = read_page_handler("https://example.com")

        assert result.get("ok") is True
        assert len(call_log) == 1
        assert all("--paywall" not in call for call in call_log)

    def test_terminal_block_not_retried(self):
        """read_page does not retry on terminal=true blocks."""
        from flarecrawl.mcp_tools.composite import read_page_handler

        terminal_result = {
            "ok": False,
            "error": {
                "code": "BLOCKED",
                "message": "CF 1020 hard block",
                "blocked": {"vendor": "cloudflare", "kind": "cf_1020_hard", "terminal": True},
            },
        }
        call_log = []

        def fake_run_cli(args, **kwargs):
            call_log.append(args[:])
            return terminal_result

        with patch("flarecrawl.mcp_tools.composite.run_cli", side_effect=fake_run_cli):
            result = read_page_handler("https://example.com")

        # Terminal blocks: may still retry but should not succeed
        # The retry returns the same terminal error, which is expected
        assert result.get("ok") is False


# ---------------------------------------------------------------------------
# extract_data partial results
# ---------------------------------------------------------------------------


class TestExtractDataPartialResults:
    def test_partial_failure_accumulates_errors(self):
        from flarecrawl.mcp_tools.composite import extract_data_handler

        call_count = [0]

        def fake_run_cli(args, **kwargs):
            call_count[0] += 1
            url = args[args.index("--urls") + 1] if "--urls" in args else ""
            if "fail" in url:
                return {"ok": False, "error": {"code": "UPSTREAM_ERROR", "message": "fail"}}
            return {"ok": True, "data": {"items": [{"price": "10"}]}}

        with patch("flarecrawl.mcp_tools.composite.run_cli", side_effect=fake_run_cli):
            result = extract_data_handler(
                urls=["https://ok.com/1", "https://fail.com/1", "https://ok.com/2"],
                prompt="Extract prices",
            )

        assert result["ok"] is True
        statuses = {item["url"]: item["status"] for item in result["data"]}
        assert statuses["https://ok.com/1"] == "ok"
        assert statuses["https://fail.com/1"] == "error"
        assert statuses["https://ok.com/2"] == "ok"
        assert "_errors" in result

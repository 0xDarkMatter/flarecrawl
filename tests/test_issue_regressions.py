"""Regression tests for reported GitHub issues.

- Issue #2: `crawl --wait -o out.json` must flush completed records on timeout.
- Issue #3: CDP commands must report a missing-`websockets` dependency clearly,
  not a misleading "Not authenticated", and must do so before the auth check.
"""

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from flarecrawl.cli import app
from flarecrawl.client import FlareCrawlError

runner = CliRunner()


class TestCrawlTimeoutRecovery:
    """Issue #2 — a crawl that times out mid-job must still save what completed."""

    def test_timeout_saves_partial_records_to_output(self, mock_credentials, tmp_path):
        out = tmp_path / "out.json"
        records = [
            {"url": "https://a.test", "markdown": "A", "status": "completed"},
            {"url": "https://b.test", "markdown": "B", "status": "completed"},
        ]

        def fake_start(self, url, **kwargs):
            return "job-timeout-1"

        def fake_wait(self, job_id, **kwargs):
            raise FlareCrawlError("Crawl timed out after 600s", "TIMEOUT")

        def fake_status(self, job_id):
            return {"status": "running", "finished": 2, "total": 3}

        def fake_get_all(self, job_id, status=None):
            yield from records

        with patch("flarecrawl.client.Client.crawl_start", new=fake_start), \
             patch("flarecrawl.client.Client.crawl_wait", new=fake_wait), \
             patch("flarecrawl.client.Client.crawl_status", new=fake_status), \
             patch("flarecrawl.client.Client.crawl_get_all", new=fake_get_all):
            result = runner.invoke(app, [
                "crawl", "https://example.com",
                "--wait", "--limit", "3",
                "-o", str(out),
            ])

        assert result.exit_code == 0, result.output
        assert out.exists(), "output file should be written even on timeout"
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["status"] == "timeout"
        assert len(data["records"]) == 2
        assert {r["url"] for r in data["records"]} == {"https://a.test", "https://b.test"}

    def test_non_timeout_error_still_aborts(self, mock_credentials, tmp_path):
        """A genuine error (not TIMEOUT) must NOT silently write a partial file."""
        out = tmp_path / "out.json"

        def fake_start(self, url, **kwargs):
            return "job-err-1"

        def fake_wait(self, job_id, **kwargs):
            raise FlareCrawlError("boom", "ERROR")

        with patch("flarecrawl.client.Client.crawl_start", new=fake_start), \
             patch("flarecrawl.client.Client.crawl_wait", new=fake_wait):
            result = runner.invoke(app, [
                "crawl", "https://example.com", "--wait", "-o", str(out),
            ])

        assert result.exit_code != 0
        assert not out.exists(), "non-timeout errors should not produce an output file"


def _cdp_invocations(tmp_path):
    """Every CLI command that depends on CDP / the optional `websockets`
    package, with a minimal invocation that reaches the CDP boundary.

    Covers both the guarded factory path (`_get_cdp_client`) and the two
    direct-construction bypasses fixed for #3 (`p6`, `recipe`). Each must
    surface the missing dependency cleanly — never a misleading "Not
    authenticated", never a raw traceback.
    """
    cookies = tmp_path / "cookies.json"
    jar = tmp_path / "jar.json"
    recipe = tmp_path / "flow.yml"
    recipe.write_text(
        "version: 1\ngoto: https://example.com\nbrowser: local\nsteps:\n  - wait: 100ms\n",
        encoding="utf-8",
    )
    return [
        # --- guarded via _get_cdp_client ---
        ("design extract", ["design", "extract", "https://example.com"]),
        ("design coherence", ["design", "coherence", "https://example.com"]),
        ("design diff", ["design", "diff", "https://a.com", "https://b.com"]),
        ("cdp connect", ["cdp", "connect"]),
        ("interact", ["interact", "https://example.com", "--click", ".x"]),
        ("webmcp discover", ["webmcp", "discover", "https://example.com"]),
        ("webmcp call", ["webmcp", "call", "https://example.com", "--tool", "t"]),
        ("videos --interactive", ["videos", "https://example.com", "--interactive"]),
        ("scrape --cdp", ["scrape", "https://example.com", "--cdp"]),
        ("scrape --interactive", ["scrape", "https://example.com", "--interactive"]),
        ("scrape --live-view", ["scrape", "https://example.com", "--live-view"]),
        ("scrape --record", ["scrape", "https://example.com", "--record"]),
        ("scrape --keep-alive", ["scrape", "https://example.com", "--keep-alive", "30"]),
        ("scrape --save-cookies", ["scrape", "https://example.com", "--save-cookies", str(cookies)]),
        ("scrape --load-cookies", ["scrape", "https://example.com", "--load-cookies", str(cookies)]),
        ("scrape --browser local", ["scrape", "https://example.com", "--browser", "local"]),
        ("tech-detect --cdp", ["tech-detect", "https://example.com", "--cdp"]),
        # --- direct CDPClient construction (the #3 bypasses) ---
        ("p6", ["p6", "https://example.com", "--jar", str(jar), "--target", "https://example.com"]),
        ("recipe", ["recipe", str(recipe)]),
    ]


def _has_traceback(result) -> bool:
    """An uncaught exception that isn't the clean typer.Exit/SystemExit path
    means the user saw a Python traceback. That's the #3 failure mode."""
    exc = result.exception
    return exc is not None and not isinstance(exc, SystemExit)


class TestCdpWebsocketsGuard:
    """Issue #3 — missing `websockets` must surface as a dependency error, early,
    for EVERY CDP-dependent command, with or without credentials."""

    @pytest.mark.parametrize("creds", [True, False], ids=["with-creds", "no-creds"])
    def test_every_cdp_command_reports_dependency_not_auth(
        self, creds, request, tmp_path, monkeypatch
    ):
        # Apply the right credential fixture: the regression is that WITHOUT
        # creds the old code hit the auth check first ("Not authenticated"),
        # while WITH creds it later failed deep in CDPClient. Both must now
        # short-circuit to the dependency error.
        request.getfixturevalue("mock_credentials" if creds else "no_credentials")

        failures = []
        for label, args in _cdp_invocations(tmp_path):
            with patch("flarecrawl.cdp.websockets", None):
                result = runner.invoke(app, args)
            out = (result.output or "").lower()
            problems = []
            if result.exit_code != 1:
                problems.append(f"exit={result.exit_code} (want 1)")
            if "websockets" not in out:
                problems.append("message does not mention 'websockets'")
            if "not authenticated" in out or "auth login" in out:
                problems.append("misleading auth message present")
            if _has_traceback(result):
                problems.append(f"raw traceback: {type(result.exception).__name__}")
            if problems:
                failures.append(f"  [{label}] {'; '.join(problems)}\n    output={result.output!r}")

        assert not failures, "CDP dependency guard failed for:\n" + "\n".join(failures)

    def test_json_mode_emits_dependency_error_envelope(self, mock_credentials):
        """--json must yield a structured MISSING_DEPENDENCY envelope, not text
        and not a traceback. Covers both a guarded path and a bypass path."""
        for args in (
            ["design", "extract", "https://example.com", "--json"],
            ["p6", "https://example.com", "--jar", "j.json",
             "--target", "https://example.com", "--json"],
        ):
            with patch("flarecrawl.cdp.websockets", None):
                result = runner.invoke(app, args)
            assert result.exit_code == 1, result.output
            assert not _has_traceback(result), result.output
            payload = json.loads(result.output)
            assert "error" in payload, result.output
            assert "websockets" in payload["error"]["message"].lower()

    def test_install_hint_brackets_survive_rich_markup(self, mock_credentials):
        """Regression: the actionable hint `uv pip install 'flarecrawl[cdp]'`
        must render with the `[cdp]` extra intact in human (text) mode. Rich
        treats `[cdp]` as a style tag and silently strips it unless the message
        is escaped — which turned the install command into a broken one."""
        with patch("flarecrawl.cdp.websockets", None):
            result = runner.invoke(app, ["design", "extract", "https://example.com"])
        assert "flarecrawl[cdp]" in result.output, result.output

    def test_recipe_local_missing_websockets_is_clean(self, mock_credentials, tmp_path):
        """The `recipe` bypass (direct CDPClient) used to raise an uncaught
        FlareCrawlError → traceback. It must now exit cleanly."""
        recipe = tmp_path / "flow.yml"
        recipe.write_text(
            "version: 1\ngoto: https://example.com\nbrowser: local\nsteps:\n  - wait: 100ms\n",
            encoding="utf-8",
        )
        with patch("flarecrawl.cdp.websockets", None):
            result = runner.invoke(app, ["recipe", str(recipe)])
        assert result.exit_code == 1, result.output
        assert not _has_traceback(result), result.output
        assert "websockets" in result.output.lower()

    def test_recipe_dry_run_does_not_require_websockets(self, mock_credentials, tmp_path):
        """--dry-run only validates + prints the plan; it must NOT trip the
        dependency guard (no browser/CDP is launched)."""
        recipe = tmp_path / "flow.yml"
        recipe.write_text(
            "version: 1\ngoto: https://example.com\nbrowser: local\nsteps:\n  - wait: 100ms\n",
            encoding="utf-8",
        )
        with patch("flarecrawl.cdp.websockets", None):
            result = runner.invoke(app, ["recipe", str(recipe), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "websockets" not in result.output.lower()

    def test_p6_injected_mint_fn_bypasses_websockets_guard(self, tmp_path):
        """The early p6 guard must only fire for the default (CDP) minter, so
        injected minters (custom flows / tests) keep working without websockets."""
        from flarecrawl.p6 import P6Config, run_p6

        cfg = P6Config(
            mint_url="https://example.com",
            jar_path=tmp_path / "jar.json",
            targets=[],  # no targets → returns immediately, no replay
        )
        with patch("flarecrawl.cdp.websockets", None):
            # A custom mint_fn means the CDP path is never taken; the guard
            # must not raise. (No targets, so no network happens.)
            result = run_p6(cfg, mint_fn=lambda *a, **k: [], replay_fn=lambda *a, **k: None)
        assert result.targets_total == 0

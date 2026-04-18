"""Tests for the ``flarecrawl authcrawl`` CLI subcommand.

The command is covered at the parse level — we patch out
``AuthenticatedCrawler.crawl`` and ``asyncio.run`` so the flags flow
through into ``CrawlConfig`` without any real network I/O.
"""

from __future__ import annotations

import pytest
pytest.importorskip("selectolax", reason="optional dep")
pytest.importorskip("aiosqlite", reason="optional dep")
from typing import Any

import pytest
from typer.testing import CliRunner

from flarecrawl import cli as cli_mod
from flarecrawl.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _captured_cfg(monkeypatch):
    """Replace AuthenticatedCrawler so we can inspect the CrawlConfig."""
    captured: dict[str, Any] = {}

    class _FakeCrawler:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        async def crawl(self):
            if False:  # pragma: no cover
                yield None
            return

    from flarecrawl import authcrawl as _ac

    monkeypatch.setattr(_ac, "AuthenticatedCrawler", _FakeCrawler)
    # The CLI imports inside the function body — patch there too.
    import importlib

    importlib.reload(_ac)
    # After reload, patch again on the reloaded module's symbols that
    # the CLI imports — it imports from flarecrawl.authcrawl fresh on
    # each invocation, so the monkeypatch above is sufficient.
    monkeypatch.setattr(
        "flarecrawl.authcrawl.AuthenticatedCrawler", _FakeCrawler
    )
    yield captured


def test_authcrawl_command_exists():
    result = runner.invoke(app, ["authcrawl", "--help"])
    assert result.exit_code == 0
    assert "authcrawl" in result.stdout.lower() or "seed url" in result.stdout.lower()


def test_authcrawl_parses_basic(_captured_cfg):
    result = runner.invoke(
        app,
        ["authcrawl", "https://example.com/", "--limit", "1", "--ignore-robots"],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    cfg = _captured_cfg["cfg"]
    assert cfg.seed_url == "https://example.com/"
    assert cfg.max_pages == 1
    assert cfg.ignore_robots is True


def test_authcrawl_resume_flag_sets_resume_job_id(_captured_cfg):
    result = runner.invoke(
        app,
        [
            "authcrawl",
            "https://example.com/",
            "--resume",
            "JOB-ABC-123",
            "--ignore-robots",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert _captured_cfg["cfg"].resume_job_id == "JOB-ABC-123"


def test_authcrawl_adaptive_delay_toggles(_captured_cfg):
    result = runner.invoke(
        app,
        [
            "authcrawl",
            "https://example.com/",
            "--adaptive-delay",
            "--ignore-robots",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert _captured_cfg["cfg"].adaptive_delay is True


def test_authcrawl_max_attempts_flows_through(_captured_cfg):
    result = runner.invoke(
        app,
        [
            "authcrawl",
            "https://example.com/",
            "--max-attempts",
            "5",
            "--ignore-robots",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert _captured_cfg["cfg"].max_attempts == 5


def test_authcrawl_refresh_days_and_tracing_flags(_captured_cfg):
    result = runner.invoke(
        app,
        [
            "authcrawl",
            "https://example.com/",
            "--refresh-days",
            "14",
            "--tracing",
            "none",
            "--ignore-robots",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert _captured_cfg["cfg"].refresh_days == 14


def test_authcrawl_rejects_unknown_tracing_value():
    result = runner.invoke(
        app,
        [
            "authcrawl",
            "https://example.com/",
            "--tracing",
            "bogus",
            "--ignore-robots",
        ],
    )
    assert result.exit_code != 0

"""Tests for ``flarecrawl.journal``."""

from __future__ import annotations

import asyncio
import json
import pathlib

import httpx
import pytest

from flarecrawl import journal
from flarecrawl.authcrawl import AuthenticatedCrawler, CrawlConfig
from flarecrawl import shutdown as _shutdown


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path: pathlib.Path):
    """Point FORMA_HOME and FLARECRAWL_FRONTIER_DIR at a tmp area."""
    monkeypatch.setenv("FORMA_HOME", str(tmp_path / "forma"))
    monkeypatch.setenv("FLARECRAWL_FRONTIER_DIR", str(tmp_path / "jobs"))
    # Ensure `forma` CLI is "not on PATH" for deterministic tests.
    monkeypatch.setattr(journal, "_forma_on_path", lambda: None)
    _shutdown.reset()
    yield


# ---------------------------------------------------------------------------
# Module-level behaviour
# ---------------------------------------------------------------------------


def test_emit_event_writes_ndjson_fallback(tmp_path: pathlib.Path):
    journal.emit_event("started", target="example.com")
    log_dir = tmp_path / "forma" / "logs"
    files = list(log_dir.glob("*.jsonl"))
    assert len(files) == 1
    line = files[0].read_text(encoding="utf-8").strip()
    rec = json.loads(line)
    assert rec["source"] == "flarecrawl"
    assert rec["action"] == "started"
    assert rec["target"] == "example.com"
    assert rec["domain"] == "crawl"
    assert rec["level"] == "info"
    assert "ts" in rec


def test_emit_event_includes_counts_and_duration(tmp_path: pathlib.Path):
    journal.emit_event(
        "completed",
        target="example.com",
        duration_ms=1234,
        counts={"urls": 5, "dead": 1},
    )
    files = list((tmp_path / "forma" / "logs").glob("*.jsonl"))
    rec = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert rec["duration_ms"] == 1234
    assert rec["counts"] == {"urls": 5, "dead": 1}


def test_emit_event_shells_to_forma_cli_when_available(monkeypatch, tmp_path):
    # Pretend forma is on PATH.
    monkeypatch.setattr(journal, "_forma_on_path", lambda: "/usr/bin/forma")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(journal.subprocess, "run", fake_run)
    journal.emit_event("started", target="example.com")

    assert captured["cmd"] == ["/usr/bin/forma", "log", "emit"]
    payload = json.loads(captured["input"])
    assert payload["action"] == "started"
    # The file-fallback should NOT have been hit.
    assert not any((tmp_path / "forma" / "logs").glob("*.jsonl")) \
        if (tmp_path / "forma" / "logs").exists() else True


def test_emit_event_silent_noop_when_nothing_writable(monkeypatch, tmp_path):
    """If FORMA_HOME cannot be created and no CLI, emit_event is silent."""
    bad = tmp_path / "does_not_exist_read_only"
    monkeypatch.setenv("FORMA_HOME", str(bad))
    monkeypatch.setattr(journal, "_forma_on_path", lambda: None)
    # Patch mkdir to always raise so the fallback is unreachable.
    real_mkdir = pathlib.Path.mkdir

    def fail_mkdir(self, *a, **kw):
        raise OSError("simulated read-only")

    monkeypatch.setattr(pathlib.Path, "mkdir", fail_mkdir)
    try:
        journal.emit_event("started", target="x")  # must not raise
    finally:
        monkeypatch.setattr(pathlib.Path, "mkdir", real_mkdir)


def test_build_record_preserves_keys():
    rec = journal._build_record(
        "completed",
        domain="crawl",
        target="example.com",
        level="info",
        duration_ms=10,
        counts={"urls": 2},
        msg="ok",
    )
    assert rec["source"] == "flarecrawl"
    assert rec["action"] == "completed"
    assert rec["target"] == "example.com"
    assert rec["duration_ms"] == 10
    assert rec["counts"] == {"urls": 2}
    assert rec["msg"] == "ok"
    assert "ts" in rec


# ---------------------------------------------------------------------------
# Integration — crawl lifecycle emits started + completed
# ---------------------------------------------------------------------------


def _mock_session_builder(handler):
    transport = httpx.MockTransport(handler)

    def _builder():
        return httpx.AsyncClient(transport=transport, follow_redirects=True)

    return _builder


def test_crawl_emits_started_and_completed(tmp_path: pathlib.Path):
    def handler(req):
        return httpx.Response(200, text="<html><body>ok</body></html>")

    cfg = CrawlConfig(
        seed_url="https://example.com/",
        max_pages=1,
        max_depth=0,
        delay=0,
        rate_limit=None,
        ignore_robots=True,
    )
    crawler = AuthenticatedCrawler(cfg)
    crawler._build_session = _mock_session_builder(handler)  # type: ignore[method-assign]

    async def run():
        async for _ in crawler.crawl():
            pass

    asyncio.run(run())

    files = list((tmp_path / "forma" / "logs").glob("*.jsonl"))
    assert files, "expected journal NDJSON file"
    lines = [json.loads(L) for L in files[0].read_text(encoding="utf-8").splitlines()]
    actions = [rec["action"] for rec in lines]
    assert "started" in actions
    assert "completed" in actions
    completed = next(r for r in lines if r["action"] == "completed")
    assert completed["counts"]["urls"] >= 1
    assert completed["target"] == "example.com"

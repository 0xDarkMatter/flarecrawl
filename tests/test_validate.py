"""Tests for :mod:`flarecrawl._validate` and its boundary wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite", reason="optional dep")

from flarecrawl._validate import JOB_ID_RE, validate_job_id


# ---------------------------------------------------------------------------
# Positive cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "job_id",
    [
        "abc",
        "A",
        "my-job_42",
        "job.v2",
        "01234567890abcdef",
        "a" * 128,
        "Z_.-0",
    ],
)
def test_valid_job_ids_pass(job_id: str) -> None:
    assert validate_job_id(job_id) == job_id
    assert JOB_ID_RE.match(job_id)


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad",
    [
        "../x",
        "a/b",
        "a\\b",
        "",
        "x" * 129,
        "a b",
        "job\x00null",
        "job;rm -rf",
        "héllo",
        "job\n",
    ],
)
def test_invalid_job_ids_raise(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid job_id"):
        validate_job_id(bad)


def test_non_string_raises() -> None:
    with pytest.raises(ValueError, match="invalid job_id"):
        validate_job_id(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Frontier.open boundary
# ---------------------------------------------------------------------------
def test_frontier_open_rejects_bad_job_id(tmp_path: Path) -> None:
    from flarecrawl.frontier_v2 import Frontier

    async def _run() -> None:
        with pytest.raises(ValueError, match="invalid job_id"):
            await Frontier.open("../evil", base_dir=tmp_path)

    asyncio.run(_run())


def test_frontier_open_accepts_valid_job_id(tmp_path: Path) -> None:
    from flarecrawl.frontier_v2 import Frontier

    async def _run() -> None:
        fr = await Frontier.open("ok-job", base_dir=tmp_path)
        try:
            assert (tmp_path / "ok-job.sqlite").exists()
        finally:
            await fr.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# CLI boundary
# ---------------------------------------------------------------------------
def test_cli_dead_letter_rejects_bad_job_id() -> None:
    from typer.testing import CliRunner

    from flarecrawl.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["frontier", "dead-letter", "../escape"])
    assert result.exit_code != 0
    assert "invalid job_id" in (result.output or "")


def test_cli_authcrawl_resume_rejects_bad_job_id() -> None:
    pytest.importorskip("selectolax", reason="optional dep")
    from typer.testing import CliRunner

    from flarecrawl.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["authcrawl", "http://example.com", "--resume", "../evil"]
    )
    assert result.exit_code != 0
    assert "invalid job_id" in (result.output or "")

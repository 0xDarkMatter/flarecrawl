"""Tests for the dead_letter helper + formatter."""

from __future__ import annotations

import pytest
pytest.importorskip("selectolax", reason="optional dep")
pytest.importorskip("aiosqlite", reason="optional dep")
import asyncio
from pathlib import Path

from flarecrawl.dead_letter import dump_dead_letter, format_rows
from flarecrawl.frontier_v2 import Frontier


def test_dump_and_format(tmp_path: Path) -> None:
    async def run() -> list[dict]:
        f = await Frontier.open("dl-job", resume=False, base_dir=tmp_path)
        await f.queue.add("http://a.example/x", depth=0, max_attempts=1)
        [item] = await f.queue.next_batch(1)
        await f.queue.mark_retry(item.fp, "boom")  # goes dead at max=1
        await f.checkpoint()
        await f.close()
        return await dump_dead_letter("dl-job", base_dir=tmp_path)

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0]["url"] == "http://a.example/x"
    assert isinstance(rows[0]["fp"], str)  # hex-stringified
    table = format_rows(rows)
    assert "http://a.example/x" in table
    assert "boom" in table
    js = format_rows(rows, as_json=True)
    assert '"boom"' in js


def test_format_empty() -> None:
    assert "no dead rows" in format_rows([])

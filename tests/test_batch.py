"""Tests for batch processing module."""

import asyncio
import json

import pytest

from flarecrawl.batch import parse_batch_file, process_batch


class TestParseBatchFile:
    """Test auto-detection and parsing of batch input files."""

    def test_plain_text(self, tmp_path):
        f = tmp_path / "urls.txt"
        f.write_text("https://example.com\nhttps://test.com\n")
        result = parse_batch_file(f)
        assert result == ["https://example.com", "https://test.com"]

    def test_plain_text_skips_blanks_and_comments(self, tmp_path):
        f = tmp_path / "urls.txt"
        f.write_text("https://example.com\n\n# a comment\nhttps://test.com\n  \n")
        result = parse_batch_file(f)
        assert result == ["https://example.com", "https://test.com"]

    def test_json_array(self, tmp_path):
        f = tmp_path / "urls.json"
        f.write_text(json.dumps(["https://a.com", "https://b.com"]))
        result = parse_batch_file(f)
        assert result == ["https://a.com", "https://b.com"]

    def test_ndjson(self, tmp_path):
        f = tmp_path / "urls.ndjson"
        f.write_text('{"url": "https://a.com"}\n{"url": "https://b.com"}\n')
        result = parse_batch_file(f)
        assert result == [{"url": "https://a.com"}, {"url": "https://b.com"}]

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = parse_batch_file(f)
        assert result == []

    def test_whitespace_only(self, tmp_path):
        f = tmp_path / "blank.txt"
        f.write_text("   \n  \n")
        result = parse_batch_file(f)
        assert result == []


class TestProcessBatch:
    """Test parallel batch processing."""

    def test_basic_parallel(self):
        async def _process(item):
            return {"value": item}

        results = asyncio.run(process_batch(["a", "b", "c"], _process))
        assert len(results) == 3
        assert all(r["status"] == "ok" for r in results)
        # All indices present
        indices = {r["index"] for r in results}
        assert indices == {0, 1, 2}

    def test_partial_failure(self):
        async def _process(item):
            if item == "fail":
                raise ValueError("boom")
            return {"value": item}

        results = asyncio.run(process_batch(["ok", "fail", "ok2"], _process))
        assert len(results) == 3

        ok_results = [r for r in results if r["status"] == "ok"]
        err_results = [r for r in results if r["status"] == "error"]
        assert len(ok_results) == 2
        assert len(err_results) == 1
        assert err_results[0]["error"]["code"] == "ERROR"
        assert "boom" in err_results[0]["error"]["message"]

    def test_error_captures_custom_code(self):
        class CustomError(Exception):
            def __init__(self, msg, code):
                super().__init__(msg)
                self.code = code

        async def _process(item):
            raise CustomError("denied", "FORBIDDEN")

        results = asyncio.run(process_batch(["x"], _process))
        assert results[0]["status"] == "error"
        assert results[0]["error"]["code"] == "FORBIDDEN"

    def test_progress_callback(self):
        calls = []

        def _on_progress(completed, total, errors):
            calls.append((completed, total, errors))

        async def _process(item):
            return {"v": item}

        asyncio.run(process_batch(["a", "b"], _process, on_progress=_on_progress))
        assert len(calls) == 2
        # Last call should show 2/2 completed
        assert calls[-1][0] == 2
        assert calls[-1][1] == 2

    def test_semaphore_bounds_concurrency(self):
        """Verify that concurrency is bounded by workers count."""
        peak = 0
        current = 0
        lock = asyncio.Lock()

        async def _process(item):
            nonlocal peak, current
            async with lock:
                current += 1
                if current > peak:
                    peak = current
            await asyncio.sleep(0.05)
            async with lock:
                current -= 1
            return {"v": item}

        # 10 items with 2 workers — peak should never exceed 2
        items = list(range(10))
        asyncio.run(process_batch(items, _process, workers=2))
        assert peak <= 2

    def test_workers_default(self):
        """Default workers is 3."""
        import inspect
        sig = inspect.signature(process_batch)
        assert sig.parameters["workers"].default == 3

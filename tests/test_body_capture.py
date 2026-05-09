"""Tests for v0.24.0 P2.1: response body interception via BodyCapture."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestBodyCapturePatternMatching:
    """URL pattern matching against fnmatch globs."""

    def test_filename_glob_matches(self, tmp_path):
        from flarecrawl.cdp import BodyCapture

        bc = BodyCapture(["*.csv"], output_dir=tmp_path)
        assert bc._matches("https://example.com/data/file.csv", "text/csv")
        assert bc._matches("https://example.com/x/y/z/manifest.csv", "text/csv")

    def test_filename_glob_no_match(self, tmp_path):
        from flarecrawl.cdp import BodyCapture

        bc = BodyCapture(["*.csv"], output_dir=tmp_path)
        assert not bc._matches("https://example.com/data/file.json", "application/json")

    def test_full_url_glob(self, tmp_path):
        from flarecrawl.cdp import BodyCapture

        bc = BodyCapture(["*api/*"], output_dir=tmp_path)
        # fnmatch wildcards do NOT cross /, so 'api/*' is unlikely to match.
        # fnmatch on a path with '/' chars uses the unix style; keep this realistic.
        assert bc._matches("https://example.com/v1/api/foo", "application/json")

    def test_multiple_patterns(self, tmp_path):
        from flarecrawl.cdp import BodyCapture

        bc = BodyCapture(["*.csv", "*.json"], output_dir=tmp_path)
        assert bc._matches("https://x.com/a.csv", "text/csv")
        assert bc._matches("https://x.com/b.json", "application/json")
        assert not bc._matches("https://x.com/c.html", "text/html")

    def test_content_type_filter_allows(self, tmp_path):
        from flarecrawl.cdp import BodyCapture

        bc = BodyCapture(
            ["*.csv"],
            output_dir=tmp_path,
            content_types=["text/csv"],
        )
        assert bc._matches("https://x.com/a.csv", "text/csv; charset=utf-8")

    def test_content_type_filter_blocks(self, tmp_path):
        from flarecrawl.cdp import BodyCapture

        bc = BodyCapture(
            ["*.csv"],
            output_dir=tmp_path,
            content_types=["application/json"],
        )
        # Pattern matches the URL but content-type filter blocks
        assert not bc._matches("https://x.com/a.csv", "text/csv")


class TestBodyCaptureBodyFetch:
    """End-to-end: response received → body fetched via Network.getResponseBody → file written."""

    def test_text_body_written(self, tmp_path):
        from flarecrawl.cdp import BodyCapture

        bc = BodyCapture(["*.csv"], output_dir=tmp_path)

        # Simulate response received
        bc._on_response_received({
            "requestId": "req1",
            "response": {
                "url": "https://example.com/data.csv",
                "mimeType": "text/csv",
                "status": 200,
            },
        })
        assert "req1" in bc._pending

        # Mock page.send to return the CSV body
        page = MagicMock()
        page.send = AsyncMock(return_value={
            "body": "col1,col2\n1,2\n3,4\n",
            "base64Encoded": False,
        })

        asyncio.run(bc.fetch_pending_bodies(page))

        # File written
        out = tmp_path / "data.csv"
        assert out.exists()
        assert out.read_text() == "col1,col2\n1,2\n3,4\n"

        # Captured metadata recorded
        assert len(bc.captured) == 1
        cap = bc.captured[0]
        assert cap["url"] == "https://example.com/data.csv"
        assert cap["size"] == len("col1,col2\n1,2\n3,4\n")
        assert cap["content_type"] == "text/csv"
        assert cap["status"] == 200

    def test_binary_body_base64(self, tmp_path):
        from flarecrawl.cdp import BodyCapture

        bc = BodyCapture(["*.bin"], output_dir=tmp_path)

        bc._on_response_received({
            "requestId": "req1",
            "response": {
                "url": "https://x.com/blob.bin",
                "mimeType": "application/octet-stream",
                "status": 200,
            },
        })

        raw_bytes = bytes(range(256))
        b64 = base64.b64encode(raw_bytes).decode()

        page = MagicMock()
        page.send = AsyncMock(return_value={"body": b64, "base64Encoded": True})

        asyncio.run(bc.fetch_pending_bodies(page))

        out = tmp_path / "blob.bin"
        assert out.read_bytes() == raw_bytes

    def test_max_body_bytes_skipped(self, tmp_path):
        from flarecrawl.cdp import BodyCapture

        bc = BodyCapture(["*.bin"], output_dir=tmp_path, max_body_bytes=10)
        bc._on_response_received({
            "requestId": "req1",
            "response": {
                "url": "https://x.com/big.bin",
                "mimeType": "application/octet-stream",
                "status": 200,
            },
        })

        page = MagicMock()
        page.send = AsyncMock(return_value={"body": "x" * 100, "base64Encoded": False})

        asyncio.run(bc.fetch_pending_bodies(page))

        # Body exceeded max — no file, no captured entry
        assert not (tmp_path / "big.bin").exists()
        assert bc.captured == []

    def test_collision_handling(self, tmp_path):
        from flarecrawl.cdp import BodyCapture

        # Pre-create the destination
        (tmp_path / "data.csv").write_text("existing")

        bc = BodyCapture(["*.csv"], output_dir=tmp_path)
        bc._on_response_received({
            "requestId": "req1",
            "response": {
                "url": "https://x.com/data.csv",
                "mimeType": "text/csv",
                "status": 200,
            },
        })

        page = MagicMock()
        page.send = AsyncMock(return_value={"body": "new content", "base64Encoded": False})

        asyncio.run(bc.fetch_pending_bodies(page))

        # Should have written to data.1.csv to avoid collision
        assert (tmp_path / "data.csv").read_text() == "existing"
        assert (tmp_path / "data.1.csv").read_text() == "new content"

    def test_fallback_filename_for_url_without_extension(self, tmp_path):
        from flarecrawl.cdp import BodyCapture

        bc = BodyCapture(["*"], output_dir=tmp_path, content_types=["application/json"])
        bc._on_response_received({
            "requestId": "abcdef0123456789",
            "response": {
                "url": "https://api.example.com/v1/data",
                "mimeType": "application/json",
                "status": 200,
            },
        })

        page = MagicMock()
        page.send = AsyncMock(return_value={"body": '{"k":"v"}', "base64Encoded": False})

        asyncio.run(bc.fetch_pending_bodies(page))

        # Should have created capture-abcdef012345.json
        files = list(tmp_path.glob("capture-*.json"))
        assert len(files) == 1
        assert "abcdef" in files[0].name

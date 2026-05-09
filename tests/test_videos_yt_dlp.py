"""Tests for v0.25.0 P3.2: yt-dlp passthrough on videos discovery."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


class TestIsYtDlpCandidate:
    def test_youtube_matches(self):
        from flarecrawl.videos import is_yt_dlp_candidate
        assert is_yt_dlp_candidate("https://www.youtube.com/watch?v=abc")
        assert is_yt_dlp_candidate("https://youtu.be/abc")

    def test_dvids_matches(self):
        from flarecrawl.videos import is_yt_dlp_candidate
        assert is_yt_dlp_candidate("https://www.dvidshub.net/video/12345")

    def test_random_url_no_match(self):
        from flarecrawl.videos import is_yt_dlp_candidate
        assert not is_yt_dlp_candidate("https://random-blog.com/post")

    def test_case_insensitive(self):
        from flarecrawl.videos import is_yt_dlp_candidate
        assert is_yt_dlp_candidate("https://WWW.YOUTUBE.COM/watch?v=abc")


class TestResolveViaYtDlp:
    def test_returns_empty_when_yt_dlp_missing(self, monkeypatch):
        from flarecrawl.videos import resolve_via_yt_dlp

        # Pretend yt_dlp is not importable
        monkeypatch.setitem(sys.modules, "yt_dlp", None)
        result = resolve_via_yt_dlp(["https://example.com/x"])
        assert result == []

    def test_basic_extraction(self, monkeypatch):
        """yt-dlp returns one info dict per URL."""
        from flarecrawl.videos import resolve_via_yt_dlp

        fake_info = {
            "url": "https://cdn.example.com/file.mp4",
            "ext": "mp4",
            "title": "My Video",
            "thumbnail": "https://example.com/thumb.jpg",
            "duration": 90,
            "extractor": "dvidshub",
        }

        fake_ydl = MagicMock()
        fake_ydl.__enter__ = MagicMock(return_value=fake_ydl)
        fake_ydl.__exit__ = MagicMock(return_value=False)
        fake_ydl.extract_info = MagicMock(return_value=fake_info)

        fake_module = MagicMock()
        fake_module.YoutubeDL.return_value = fake_ydl
        monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)

        results = resolve_via_yt_dlp(["https://www.dvidshub.net/video/123"])
        assert len(results) == 1
        r = results[0]
        assert r.url == "https://cdn.example.com/file.mp4"
        assert r.title == "My Video"
        assert r.format == "mp4"
        assert r.type == "yt-dlp"
        assert r.source_element == "dvidshub"

    def test_extractor_failure_silently_skipped(self, monkeypatch):
        from flarecrawl.videos import resolve_via_yt_dlp

        fake_ydl = MagicMock()
        fake_ydl.__enter__ = MagicMock(return_value=fake_ydl)
        fake_ydl.__exit__ = MagicMock(return_value=False)
        fake_ydl.extract_info = MagicMock(side_effect=Exception("not supported"))

        fake_module = MagicMock()
        fake_module.YoutubeDL.return_value = fake_ydl
        monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)

        result = resolve_via_yt_dlp(["https://obscure-host.com/x"])
        assert result == []

    def test_playlist_entries_flattened(self, monkeypatch):
        """When yt-dlp returns a playlist (entries field), each entry yields a result."""
        from flarecrawl.videos import resolve_via_yt_dlp

        fake_info = {
            "_type": "playlist",
            "entries": [
                {"url": "https://x.com/a.mp4", "ext": "mp4", "title": "A", "extractor": "youtube"},
                {"url": "https://x.com/b.mp4", "ext": "mp4", "title": "B", "extractor": "youtube"},
            ],
        }

        fake_ydl = MagicMock()
        fake_ydl.__enter__ = MagicMock(return_value=fake_ydl)
        fake_ydl.__exit__ = MagicMock(return_value=False)
        fake_ydl.extract_info = MagicMock(return_value=fake_info)

        fake_module = MagicMock()
        fake_module.YoutubeDL.return_value = fake_ydl
        monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)

        results = resolve_via_yt_dlp(["https://www.youtube.com/playlist?list=foo"])
        assert len(results) == 2
        urls = sorted(r.url for r in results)
        assert urls == ["https://x.com/a.mp4", "https://x.com/b.mp4"]


class TestExtractVideosUseYtDlp:
    def test_use_yt_dlp_false_does_not_invoke(self, monkeypatch):
        """Default behaviour shouldn't import yt_dlp."""
        from flarecrawl.videos import extract_videos

        called = {"x": False}
        def faux_resolve(_urls):
            called["x"] = True
            return []
        monkeypatch.setattr("flarecrawl.videos.resolve_via_yt_dlp", faux_resolve)

        html = '<html><body><iframe src="https://www.youtube.com/embed/abc"></iframe></body></html>'
        results = extract_videos(html, "https://example.com")
        assert called["x"] is False
        # Still got the iframe-discovered embed
        assert any("youtube" in r.url.lower() for r in results)

    def test_use_yt_dlp_true_invokes_for_candidates(self, monkeypatch):
        from flarecrawl.videos import VideoResult, extract_videos

        called: dict = {"urls": None}
        def faux_resolve(urls):
            urls = list(urls)
            called["urls"] = urls
            return [VideoResult(
                url="https://cdn.x.com/resolved.mp4",
                type="yt-dlp", format="mp4", title="resolved",
                source_element="youtube",
            )]
        monkeypatch.setattr("flarecrawl.videos.resolve_via_yt_dlp", faux_resolve)

        html = '<html><body><iframe src="https://www.youtube.com/embed/abc"></iframe></body></html>'
        results = extract_videos(html, "https://example.com", use_yt_dlp=True)

        assert called["urls"] is not None
        assert any("youtube" in u.lower() for u in called["urls"])
        assert any(r.type == "yt-dlp" for r in results)

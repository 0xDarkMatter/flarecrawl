"""Tests for video discovery and Netscape cookie export."""

from __future__ import annotations

import pytest

from flarecrawl.videos import extract_videos


class TestExtractVideos:
    """Test video extraction from HTML."""

    def test_video_element(self):
        html = '<video src="https://example.com/video.mp4" poster="thumb.jpg"></video>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].format == "mp4"
        assert results[0].type == "direct"

    def test_video_source_elements(self):
        html = '<video><source src="video.mp4" type="video/mp4"><source src="video.webm" type="video/webm"></video>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 2

    def test_youtube_embed(self):
        html = '<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].format == "youtube"
        assert "watch?v=" in results[0].url

    def test_youtube_nocookie_embed(self):
        html = '<iframe src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"></iframe>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_vimeo_embed(self):
        html = '<iframe src="https://player.vimeo.com/video/123456"></iframe>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].url == "https://vimeo.com/123456"

    def test_dailymotion_embed(self):
        html = '<iframe src="https://www.dailymotion.com/embed/video/x8abc12"></iframe>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].format == "dailymotion"

    def test_og_video(self):
        html = '<meta property="og:video" content="https://example.com/video.mp4">'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].type == "og"

    def test_og_video_url(self):
        html = '<meta property="og:video:url" content="https://example.com/clip.mp4">'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].type == "og"

    def test_jsonld_video(self):
        html = '<script type="application/ld+json">{"@type":"VideoObject","contentUrl":"https://example.com/v.mp4","name":"Test"}</script>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].title == "Test"

    def test_jsonld_embed_url(self):
        html = '<script type="application/ld+json">{"@type":"VideoObject","embedUrl":"https://example.com/embed/v.mp4","name":"Embed"}</script>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].type == "jsonld"

    def test_direct_link(self):
        html = '<a href="https://cdn.example.com/download.mp4">Download Video</a>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1

    def test_m3u8_in_script(self):
        html = '<script>var streamUrl = "https://cdn.example.com/live/stream.m3u8";</script>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].format == "m3u8"

    def test_mpd_in_script(self):
        html = '<script>const dash = "https://cdn.example.com/manifest.mpd";</script>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].format == "mpd"

    def test_deduplication(self):
        html = '<video src="https://example.com/v.mp4"></video><a href="https://example.com/v.mp4">Link</a>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1

    def test_no_videos(self):
        html = "<h1>No videos here</h1><p>Just text</p>"
        results = extract_videos(html, "https://example.com")
        assert len(results) == 0

    def test_relative_urls(self):
        html = '<video src="/videos/lecture.mp4"></video>'
        results = extract_videos(html, "https://example.com")
        assert results[0].url == "https://example.com/videos/lecture.mp4"

    def test_data_src_attribute(self):
        html = '<div data-src="https://example.com/lazy.mp4"></div>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].format == "mp4"

    def test_data_video_url_attribute(self):
        html = '<div data-video-url="https://example.com/clip.webm"></div>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].format == "webm"

    def test_poster_as_thumbnail(self):
        html = '<video src="https://example.com/v.mp4" poster="https://example.com/thumb.jpg"></video>'
        results = extract_videos(html, "https://example.com")
        assert results[0].thumbnail == "https://example.com/thumb.jpg"

    def test_sorting_direct_before_embed(self):
        html = (
            '<iframe src="https://www.youtube.com/embed/abc123"></iframe>'
            '<video src="https://example.com/v.mp4"></video>'
        )
        results = extract_videos(html, "https://example.com")
        assert len(results) == 2
        assert results[0].type == "direct"
        assert results[1].type == "embed"

    def test_malformed_jsonld_skipped(self):
        html = '<script type="application/ld+json">not valid json</script>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 0

    def test_multiple_sources(self):
        html = """
        <video src="https://example.com/a.mp4"></video>
        <iframe src="https://www.youtube.com/embed/xyz789"></iframe>
        <meta property="og:video" content="https://example.com/b.mp4">
        <a href="https://example.com/c.webm">Download</a>
        """
        results = extract_videos(html, "https://example.com")
        assert len(results) == 4


class TestCookieExport:
    """Test Netscape cookie file export."""

    def test_netscape_format(self, tmp_path):
        from flarecrawl.cookies import cookies_to_netscape

        cookies = [
            {"name": "session", "value": "abc123", "domain": ".example.com", "path": "/", "secure": True, "expires": 0},
            {"name": "pref", "value": "dark", "domain": "example.com", "path": "/", "secure": False, "expires": 1700000000},
        ]
        out = tmp_path / "cookies.txt"
        cookies_to_netscape(cookies, out)
        content = out.read_text()
        assert "# Netscape HTTP Cookie File" in content
        assert ".example.com\tTRUE\t/\tTRUE\t0\tsession\tabc123" in content
        assert "example.com\tFALSE\t/\tFALSE\t1700000000\tpref\tdark" in content

    def test_netscape_empty_cookies(self, tmp_path):
        from flarecrawl.cookies import cookies_to_netscape

        out = tmp_path / "cookies.txt"
        cookies_to_netscape([], out)
        content = out.read_text()
        assert "# Netscape HTTP Cookie File" in content
        lines = [l for l in content.strip().splitlines() if not l.startswith("#") and l.strip()]
        assert len(lines) == 0


class TestVideosCLI:
    """Test videos command CLI integration."""

    def test_videos_in_help(self):
        from flarecrawl.cli import app
        from typer.testing import CliRunner

        result = CliRunner().invoke(app, ["videos", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output
        assert "--export-cookies" in result.output
        assert "yt-dlp" in result.output

    def test_videos_in_main_help(self):
        from flarecrawl.cli import app
        from typer.testing import CliRunner

        result = CliRunner().invoke(app, ["--help"])
        assert "videos" in result.output

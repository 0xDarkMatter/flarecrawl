"""Live video discovery tests against real websites.

Run: PYTHONPATH=src pytest tests/live/test_videos_live.py -v -s
Requires: CF auth configured (flarecrawl auth login)

Tests real video discovery across platforms, news sites, embeds,
and edge cases. Content changes over time so we test for format
detection and filtering behaviour, not exact counts.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from flarecrawl.cli import app

runner = CliRunner()


def _videos(url: str, js: bool = False) -> dict:
    """Run flarecrawl videos and return parsed JSON."""
    args = ["videos", url, "--json"]
    if js:
        args.append("--js")
    result = runner.invoke(app, args)
    assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output[:300]}"
    return json.loads(result.output)


def _has_format(data: list, fmt: str) -> bool:
    return any(v["format"] == fmt for v in data)


def _has_type(data: list, vtype: str) -> bool:
    return any(v["type"] == vtype for v in data)


def _no_ads(data: list) -> bool:
    ad_domains = ["googlesyndication", "doubleclick", "adserver", "pagead",
                  "facebook.com/tr", "analytics", "taboola", "outbrain", "criteo"]
    for v in data:
        for ad in ad_domains:
            if ad in v["url"]:
                return False
    return True


def _no_blobs(data: list) -> bool:
    return all(not v["url"].startswith("blob:") for v in data)


# ==================================================================
# Page URL detection — 21 platforms
# ==================================================================


@pytest.mark.live
class TestPageURLYouTube:
    def test_youtube_watch(self, has_cf_auth):
        d = _videos("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "youtube")
        assert any("watch?v=dQw4w9WgXcQ" in v["url"] for v in d["data"])

    def test_youtube_short_url(self, has_cf_auth):
        d = _videos("https://youtu.be/dQw4w9WgXcQ")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "youtube")

    def test_youtube_no_blob(self, has_cf_auth):
        d = _videos("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert _no_blobs(d["data"])

    def test_youtube_normalises_embed_in_og(self, has_cf_auth):
        """YouTube og:video contains an embed URL — should be normalised to watch URL."""
        d = _videos("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        for v in d["data"]:
            assert "/embed/" not in v["url"], f"Embed URL not normalised: {v['url']}"


@pytest.mark.live
class TestPageURLVimeo:
    def test_vimeo_video(self, has_cf_auth):
        d = _videos("https://vimeo.com/347119375")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "vimeo")
        assert any("vimeo.com/347119375" in v["url"] for v in d["data"])


@pytest.mark.live
class TestPageURLTwitter:
    def test_x_com_video(self, has_cf_auth):
        """X.com video tweet — page URL detected."""
        d = _videos("https://x.com/NASA/status/1704213231766708714")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "twitter")

    def test_twitter_old_url(self, has_cf_auth):
        d = _videos("https://twitter.com/NASA/status/1704213231766708714")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "twitter")


@pytest.mark.live
class TestPageURLTikTok:
    def test_tiktok_video(self, has_cf_auth):
        d = _videos("https://www.tiktok.com/@nasa/video/7289345681234567?")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "tiktok")


@pytest.mark.live
class TestPageURLFacebook:
    def test_fb_watch(self, has_cf_auth):
        d = _videos("https://www.facebook.com/watch/?v=1234567890")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "facebook")

    @pytest.mark.xfail(reason="fb.watch short URLs cause redirect loops in CF browser")
    def test_fb_short(self, has_cf_auth):
        d = _videos("https://fb.watch/abc123/")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "facebook")


@pytest.mark.live
class TestPageURLInstagram:
    def test_instagram_reel(self, has_cf_auth):
        d = _videos("https://www.instagram.com/reel/C1234567890/")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "instagram")

    def test_instagram_post(self, has_cf_auth):
        d = _videos("https://www.instagram.com/p/C1234567890/")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "instagram")


@pytest.mark.live
class TestPageURLOtherPlatforms:
    def test_streamable(self, has_cf_auth):
        d = _videos("https://streamable.com/moo")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "streamable")

    def test_dailymotion(self, has_cf_auth):
        d = _videos("https://www.dailymotion.com/video/x8qrstuv")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "dailymotion")

    def test_twitch_clip(self, has_cf_auth):
        d = _videos("https://clips.twitch.tv/AmazingClip-abc123")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "twitch")

    @pytest.mark.xfail(reason="Reddit bot detection kills CF browser session")
    def test_reddit_video_post(self, has_cf_auth):
        d = _videos("https://www.reddit.com/r/videos/comments/abc123/test")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "reddit")

    def test_imgur(self, has_cf_auth):
        d = _videos("https://imgur.com/a/abc123")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "imgur")

    def test_loom(self, has_cf_auth):
        d = _videos("https://www.loom.com/share/abc123def456")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "loom")

    @pytest.mark.xfail(reason="Bilibili times out from CF edge (geo/latency)")
    def test_bilibili(self, has_cf_auth):
        d = _videos("https://www.bilibili.com/video/BV1xx411c7XW")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "bilibili")


# ==================================================================
# News sites — real embedded videos in HTML
# ==================================================================


@pytest.mark.live
class TestNewsSitesVideos:
    def test_cnn_homepage(self, has_cf_auth):
        """CNN homepage has autoplay mp4 loop videos."""
        d = _videos("https://www.cnn.com")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "mp4")

    def test_cnn_no_ads(self, has_cf_auth):
        """CNN has ad scripts — verify they're filtered."""
        d = _videos("https://www.cnn.com")
        assert _no_ads(d["data"])

    def test_reuters_hls_streams(self, has_cf_auth):
        """Reuters embeds HLS m3u8 streams in inline JS."""
        d = _videos("https://www.reuters.com")
        assert d["meta"]["count"] >= 5
        assert _has_format(d["data"], "m3u8")

    def test_nbc_news_videos(self, has_cf_auth):
        """NBC News has both m3u8 and mp4."""
        d = _videos("https://www.nbcnews.com")
        assert d["meta"]["count"] >= 5


# ==================================================================
# Pages with embedded HTML5 video
# ==================================================================


@pytest.mark.live
class TestEmbeddedHTMLVideo:
    def test_w3schools_video_page(self, has_cf_auth):
        """W3Schools HTML5 video tutorial — direct mp4 sources."""
        d = _videos("https://www.w3schools.com/html/html5_video.asp")
        assert d["meta"]["count"] >= 1
        assert _has_format(d["data"], "mp4")
        assert any("mov_bbb" in v["url"] for v in d["data"])

    def test_w3schools_has_source_elements(self, has_cf_auth):
        """Should find <source> elements inside <video>."""
        d = _videos("https://www.w3schools.com/html/html5_video.asp")
        assert any(v.get("source_element") == "video>source" for v in d["data"])


# ==================================================================
# Edge cases
# ==================================================================


@pytest.mark.live
class TestEdgeCases:
    def test_page_with_no_videos(self, has_cf_auth):
        """example.com has no videos — should return empty."""
        d = _videos("https://example.com")
        assert d["meta"]["count"] == 0
        assert d["data"] == []

    def test_dedup_across_sources(self, has_cf_auth):
        """YouTube page — should dedup og:video vs page URL detection."""
        d = _videos("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        urls = [v["url"] for v in d["data"]]
        assert len(urls) == len(set(urls)), f"Duplicates found: {urls}"

    def test_format_consistency(self, has_cf_auth):
        """All results should have required fields."""
        d = _videos("https://www.cnn.com")
        for v in d["data"]:
            assert "url" in v
            assert "type" in v
            assert "format" in v
            assert v["url"].startswith("http")

    def test_meta_count_matches_data(self, has_cf_auth):
        """meta.count should match len(data)."""
        d = _videos("https://www.reuters.com")
        assert d["meta"]["count"] == len(d["data"])


# ==================================================================
# Stdin mode (no network needed)
# ==================================================================


@pytest.mark.local
class TestStdinVideoExtraction:
    """Test video extraction from piped HTML — no auth needed."""

    def test_video_element(self):
        html = '<video src="https://example.com/video.mp4"></video>'
        result = runner.invoke(app, ["scrape", "--stdin", "--json"], input=html)
        # We can't use videos --stdin, but we can verify the HTML parses
        assert result.exit_code == 0

    def test_multiple_video_sources(self):
        from flarecrawl.videos import extract_videos
        html = """
        <video poster="/thumb.jpg">
            <source src="/hd.mp4" type="video/mp4">
            <source src="/sd.webm" type="video/webm">
        </video>
        <iframe src="https://www.youtube.com/embed/test123"></iframe>
        <meta property="og:video" content="https://example.com/og-video.mp4">
        <script>var stream = "https://cdn.example.com/live.m3u8";</script>
        <a href="/download.mp4">Download</a>
        """
        results = extract_videos(html, "https://example.com")
        formats = {r.format for r in results}
        assert "mp4" in formats
        assert "webm" in formats
        assert "youtube" in formats
        assert "m3u8" in formats
        assert len(results) >= 5

    def test_platform_embed_detection(self):
        from flarecrawl.videos import extract_videos
        html = """
        <iframe src="https://www.youtube.com/embed/abc123"></iframe>
        <iframe src="https://player.vimeo.com/video/999"></iframe>
        <iframe src="https://www.dailymotion.com/embed/video/xyz"></iframe>
        <iframe src="https://fast.wistia.net/embed/iframe/def456"></iframe>
        <iframe src="https://www.loom.com/embed/ghi789"></iframe>
        <iframe src="https://play.vidyard.com/jkl012"></iframe>
        """
        results = extract_videos(html, "https://example.com")
        formats = {r.format for r in results}
        assert "youtube" in formats
        assert "vimeo" in formats
        assert "dailymotion" in formats
        assert "wistia" in formats
        assert "loom" in formats
        assert "vidyard" in formats
        assert len(results) == 6

    def test_ad_filtering_synthetic(self):
        from flarecrawl.videos import extract_videos
        html = """
        <video src="https://example.com/real-video.mp4"></video>
        <video src="https://pagead2.googlesyndication.com/ad.mp4"></video>
        <video src="https://ad.doubleclick.net/tracker.mp4"></video>
        <video src="https://cdn.taboola.com/promo.mp4"></video>
        """
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].url == "https://example.com/real-video.mp4"

    def test_css_background_video(self):
        from flarecrawl.videos import extract_videos
        html = '<div style="background: url(/hero-bg.mp4)"></div>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].url == "https://example.com/hero-bg.mp4"

    def test_json_ld_video_object(self):
        from flarecrawl.videos import extract_videos
        html = '''<script type="application/ld+json">{
            "@type": "VideoObject",
            "name": "Product Demo",
            "contentUrl": "https://cdn.example.com/demo.mp4",
            "thumbnailUrl": "https://cdn.example.com/thumb.jpg",
            "duration": "PT2M30S"
        }</script>'''
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].title == "Product Demo"
        assert results[0].thumbnail == "https://cdn.example.com/thumb.jpg"
        assert results[0].duration == "PT2M30S"

    def test_brightcove_video_js(self):
        from flarecrawl.videos import extract_videos
        html = '<video-js data-video-id="5550679964001" data-account="1752604059001"></video-js>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].format == "brightcove"

    def test_twitter_player_card(self):
        from flarecrawl.videos import extract_videos
        html = '''
        <meta name="twitter:player:stream" content="https://video.twimg.com/tweet_video/abc.mp4">
        <meta name="twitter:player" content="https://platform.twitter.com/embed/player/123">
        '''
        results = extract_videos(html, "https://example.com")
        assert len(results) == 2
        assert any(r.format == "mp4" for r in results)
        assert any(r.format == "twitter" for r in results)

    def test_autoplay_background_video(self):
        from flarecrawl.videos import extract_videos
        html = '<video autoplay muted loop playsinline src="https://cdn.example.com/hero.mp4" class="bg-video"></video>'
        results = extract_videos(html, "https://example.com")
        assert len(results) == 1
        assert results[0].format == "mp4"

    def test_page_url_detection_all_platforms(self):
        """Verify all page URL patterns detect correctly."""
        from flarecrawl.videos import extract_videos

        platforms = {
            "youtube": "https://www.youtube.com/watch?v=test",
            "vimeo": "https://vimeo.com/123456",
            "twitter": "https://x.com/user/status/123",
            "instagram": "https://www.instagram.com/reel/ABC/",
            "tiktok": "https://www.tiktok.com/@user/video/123",
            "facebook": "https://www.facebook.com/watch/?v=123",
            "streamable": "https://streamable.com/abc",
            "twitch": "https://clips.twitch.tv/Clip123",
            "reddit": "https://www.reddit.com/r/sub/comments/abc/title",
            "imgur": "https://imgur.com/a/abc",
            "dailymotion": "https://www.dailymotion.com/video/x8abc",
            "rumble": "https://rumble.com/vabc-title.html",
            "bitchute": "https://www.bitchute.com/video/abc/",
            "bilibili": "https://www.bilibili.com/video/BV1xx",
            "linkedin": "https://www.linkedin.com/posts/user_act-1",
            "loom": "https://www.loom.com/share/abc123",
        }

        for expected_fmt, url in platforms.items():
            results = extract_videos("<html></html>", url)
            assert len(results) >= 1, f"{expected_fmt}: no results for {url}"
            assert results[0].format == expected_fmt, f"{expected_fmt}: got {results[0].format} for {url}"

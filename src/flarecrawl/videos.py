"""Video URL discovery from web pages."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup


# Hosts where yt-dlp typically has working extractors that beat DOM scraping.
# Used to filter which iframe URLs we send through yt-dlp.extract_info.
_YT_DLP_HOSTS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "vimeo.com", "player.vimeo.com",
    "dailymotion.com",
    "twitch.tv", "clips.twitch.tv",
    "tiktok.com",
    "facebook.com",
    "dvidshub.net", "api.dvidshub.net",
    "wistia.com", "fast.wistia.net",
    "loom.com",
    "soundcloud.com",
    "rumble.com",
    "bitchute.com",
    "odysee.com",
    "twitter.com", "x.com",
)


@dataclass
class VideoResult:
    """A discovered video URL with metadata."""

    url: str
    type: str  # "direct" | "embed" | "og" | "jsonld" | "script"
    format: str  # "mp4" | "webm" | "m3u8" | "mpd" | "youtube" | "vimeo" | "unknown"
    title: str | None = None
    thumbnail: str | None = None
    duration: str | None = None
    source_element: str | None = None

    def to_dict(self) -> dict:
        """Return dict with None values stripped."""
        return {k: v for k, v in asdict(self).items() if v is not None}


# Video file extensions we recognise
_VIDEO_EXTENSIONS = (".mp4", ".webm", ".m3u8", ".mpd", ".mov", ".avi")

# Ad/tracking URL patterns to filter out
_AD_PATTERNS = re.compile(
    r"doubleclick\.net|googlesyndication|googleadservices|"
    r"facebook\.com/tr|analytics|pixel|tracking|"
    r"adserver|adform|adsystem|ad\.doubleclick|"
    r"imasdk\.googleapis|pagead|securepubads|"
    r"moat\.com|outbrain|taboola|criteo|"
    r"prebid|bidswitch|openx\.net|pubmatic",
    re.IGNORECASE,
)

# Map extension to format label
_EXT_FORMAT: dict[str, str] = {
    ".mp4": "mp4",
    ".webm": "webm",
    ".m3u8": "m3u8",
    ".mpd": "mpd",
    ".mov": "mov",
    ".avi": "avi",
}

# Regex for video URLs inside inline scripts
_SCRIPT_VIDEO_RE = re.compile(
    r'https?://[^\s"\'<>]+\.(?:mp4|webm|m3u8|mpd)(?:\?[^\s"\'<>]*)?',
    re.IGNORECASE,
)

# Embed URL patterns — major platforms
_YOUTUBE_EMBED_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]+)"
)
_VIMEO_EMBED_RE = re.compile(
    r"https?://player\.vimeo\.com/video/(\d+)"
)
_DAILYMOTION_EMBED_RE = re.compile(
    r"https?://(?:www\.)?dailymotion\.com/embed/video/([A-Za-z0-9]+)"
)

# Additional platform embed patterns
_PLATFORM_EMBEDS: list[tuple[re.Pattern, str, str]] = [
    # (regex, format_name, canonical_url_template)  — group(1) is the ID
    (re.compile(r"https?://fast\.wistia\.(?:net|com)/embed/iframe/([A-Za-z0-9]+)"), "wistia", "https://fast.wistia.net/embed/iframe/{id}"),
    (re.compile(r"https?://(?:www\.)?loom\.com/embed/([A-Za-z0-9]+)"), "loom", "https://www.loom.com/share/{id}"),
    (re.compile(r"https?://clips\.twitch\.tv/embed\?clip=([A-Za-z0-9_-]+)"), "twitch", "https://clips.twitch.tv/{id}"),
    (re.compile(r"https?://(?:www\.)?twitch\.tv/(?:videos|[^/]+/clip)/([A-Za-z0-9_-]+)"), "twitch", "https://www.twitch.tv/videos/{id}"),
    (re.compile(r"https?://(?:www\.)?tiktok\.com/embed/(?:v2/)?(\d+)"), "tiktok", "https://www.tiktok.com/video/{id}"),
    (re.compile(r"https?://(?:www\.)?facebook\.com/plugins/video"), "facebook", None),
    (re.compile(r"https?://play\.vidyard\.com/([A-Za-z0-9]+)"), "vidyard", "https://play.vidyard.com/{id}"),
    (re.compile(r"https?://(?:www\.)?rumble\.com/embed/([A-Za-z0-9]+)"), "rumble", "https://rumble.com/embed/{id}"),
    (re.compile(r"https?://(?:www\.)?bitchute\.com/embed/([A-Za-z0-9]+)"), "bitchute", "https://www.bitchute.com/video/{id}"),
    (re.compile(r"https?://player\.bilibili\.com/player\.html\?.*?bvid=([A-Za-z0-9]+)"), "bilibili", "https://www.bilibili.com/video/{id}"),
    (re.compile(r"https?://coub\.com/embed/([A-Za-z0-9]+)"), "coub", "https://coub.com/view/{id}"),
    (re.compile(r"https?://open\.spotify\.com/embed/episode/([A-Za-z0-9]+)"), "spotify", "https://open.spotify.com/episode/{id}"),
]

# Page URL patterns (detect the page itself is a video)
_YOUTUBE_PAGE_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]+)"
)
_VIMEO_PAGE_RE = re.compile(
    r"https?://(?:www\.)?vimeo\.com/(\d+)"
)

# Additional page URL patterns — sites where the URL IS the video
# These are detected from base_url so yt-dlp can handle them
_PAGE_URL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Social media
    (re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/\w+/status/(\d+)"), "twitter"),
    (re.compile(r"https?://(?:www\.)?instagram\.com/(?:reel|p)/([A-Za-z0-9_-]+)"), "instagram"),
    (re.compile(r"https?://(?:www\.)?tiktok\.com/@[^/]+/video/(\d+)"), "tiktok"),
    (re.compile(r"https?://(?:www\.)?facebook\.com/(?:watch/?\?v=|reel/|.*?/videos/)(\d+)"), "facebook"),
    (re.compile(r"https?://fb\.watch/([A-Za-z0-9_-]+)"), "facebook"),
    # Video hosting
    (re.compile(r"https?://(?:www\.)?streamable\.com/([A-Za-z0-9]+)"), "streamable"),
    (re.compile(r"https?://(?:www\.)?dailymotion\.com/video/([A-Za-z0-9]+)"), "dailymotion"),
    (re.compile(r"https?://(?:www\.)?twitch\.tv/videos/(\d+)"), "twitch"),
    (re.compile(r"https?://clips\.twitch\.tv/([A-Za-z0-9_-]+)"), "twitch"),
    (re.compile(r"https?://(?:www\.)?rumble\.com/v([A-Za-z0-9_-]+)"), "rumble"),
    (re.compile(r"https?://(?:www\.)?bitchute\.com/video/([A-Za-z0-9]+)"), "bitchute"),
    # Image/GIF sites with video
    (re.compile(r"https?://(?:www\.)?imgur\.com/(?:a/)?([A-Za-z0-9]+)"), "imgur"),
    # Chinese platforms
    (re.compile(r"https?://(?:www\.)?bilibili\.com/video/(BV[A-Za-z0-9]+)"), "bilibili"),
    # Professional
    (re.compile(r"https?://(?:www\.)?linkedin\.com/(?:posts|feed/update)/([^\s?]+)"), "linkedin"),
    (re.compile(r"https?://(?:www\.)?loom\.com/share/([A-Za-z0-9]+)"), "loom"),
    # Reddit
    (re.compile(r"https?://(?:www\.|old\.)?reddit\.com/r/\w+/comments/([A-Za-z0-9]+)"), "reddit"),
]

# Data attributes that may contain video URLs
_DATA_ATTRS = ("data-src", "data-video-url", "data-video-id")


def _guess_format(url: str) -> str:
    """Guess video format from URL path."""
    lower = url.lower().split("?")[0]
    for ext, fmt in _EXT_FORMAT.items():
        if lower.endswith(ext):
            return fmt
    return "unknown"


def _normalise_youtube(embed_url: str, video_id: str) -> str:
    """Convert YouTube embed URL to canonical watch URL."""
    return f"https://www.youtube.com/watch?v={video_id}"


def _normalise_vimeo(video_id: str) -> str:
    """Convert Vimeo player URL to canonical URL."""
    return f"https://vimeo.com/{video_id}"


def resolve_via_yt_dlp(urls: Iterable[str]) -> list[VideoResult]:
    """v0.25.0 P3.2: enrich discovered URLs with yt-dlp's extractor registry.

    yt-dlp has 1500+ extractors that resolve provider-specific URLs
    (DVIDS, YouTube unlisted, Vimeo with auth, TikTok, Twitch, etc.) to
    direct media URLs. We use it as a fallback enrichment pass, not a
    replacement: DOM scraping is much faster for the common case.

    Returns a list of VideoResult entries for URLs that yt-dlp could
    extract media info from. URLs it can't handle are silently skipped.

    Returns an empty list if yt-dlp isn't installed.
    """
    try:
        import yt_dlp  # type: ignore[import-untyped]
    except ImportError:
        return []

    out: list[VideoResult] = []
    opts: dict = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        for url in urls:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception:
                continue  # extractor errored — skip silently
            if not info:
                continue
            # extract_info may return playlist-like structures
            entries = info.get("entries") if isinstance(info, dict) else None
            items = entries if entries else [info]
            for item in items:
                if not isinstance(item, dict):
                    continue
                media_url = item.get("url") or item.get("webpage_url")
                if not media_url:
                    continue
                ext = item.get("ext", "unknown")
                title = item.get("title")
                thumbnail = item.get("thumbnail")
                duration = item.get("duration_string") or (
                    str(item.get("duration")) if item.get("duration") else None
                )
                out.append(VideoResult(
                    url=media_url,
                    type="yt-dlp",
                    format=ext,
                    title=title,
                    thumbnail=thumbnail,
                    duration=duration,
                    source_element=item.get("extractor", "yt-dlp"),
                ))
    return out


def is_yt_dlp_candidate(url: str) -> bool:
    """True if a URL is plausibly handled by yt-dlp's registry."""
    return any(host in url.lower() for host in _YT_DLP_HOSTS)


def extract_videos(html: str, base_url: str, *, use_yt_dlp: bool = False) -> list[VideoResult]:
    """Extract video URLs from HTML.

    Checks <video>, <iframe> embeds, <a> links, data-* attributes,
    OpenGraph tags, JSON-LD VideoObjects, and inline script URLs.
    Returns deduplicated results sorted by type.
    """
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    results: list[VideoResult] = []

    # 0. Detect if the page URL itself is a video page (YouTube, Vimeo)
    yt_page = _YOUTUBE_PAGE_RE.search(base_url)
    if yt_page:
        results.append(VideoResult(
            url=f"https://www.youtube.com/watch?v={yt_page.group(1)}",
            type="page", format="youtube", source_element="url",
        ))
        seen.add(results[-1].url)
    vm_page = _VIMEO_PAGE_RE.search(base_url)
    if vm_page:
        results.append(VideoResult(
            url=f"https://vimeo.com/{vm_page.group(1)}",
            type="page", format="vimeo", source_element="url",
        ))
        seen.add(results[-1].url)

    # Check additional page URL patterns (Twitter, Instagram, TikTok, etc.)
    if not results:  # only if YouTube/Vimeo didn't match
        for pattern, fmt in _PAGE_URL_PATTERNS:
            m = pattern.search(base_url)
            if m:
                results.append(VideoResult(
                    url=base_url, type="page", format=fmt, source_element="url",
                ))
                seen.add(base_url)
                break

    def _add(url: str, vtype: str, fmt: str, **kwargs) -> None:
        if not url or url in seen:
            return
        # Skip blob:, data:, javascript: URLs — not downloadable
        if url.startswith(("blob:", "data:", "javascript:")):
            return
        # Skip ad/tracking URLs
        if _AD_PATTERNS.search(url):
            return
        # Normalise YouTube embed URLs from any source
        yt = _YOUTUBE_EMBED_RE.search(url)
        if yt:
            url = _normalise_youtube(url, yt.group(1))
            fmt = "youtube"
            vtype = "embed" if vtype != "og" else vtype
        # Normalise Vimeo URLs from any source
        vm = _VIMEO_EMBED_RE.search(url)
        if vm:
            url = _normalise_vimeo(vm.group(1))
            fmt = "vimeo"
        if url in seen:
            return
        seen.add(url)
        results.append(VideoResult(url=url, type=vtype, format=fmt, **kwargs))

    # 1. <video> elements
    for video in soup.find_all("video"):
        poster = video.get("poster")
        thumb = urljoin(base_url, poster) if poster else None
        src = video.get("src")
        if src:
            abs_url = urljoin(base_url, src)
            _add(abs_url, "direct", _guess_format(abs_url),
                 thumbnail=thumb, source_element="video")
        for source in video.find_all("source"):
            s_src = source.get("src")
            if s_src:
                abs_url = urljoin(base_url, s_src)
                _add(abs_url, "direct", _guess_format(abs_url),
                     thumbnail=thumb, source_element="video>source")

    # 2. <iframe> embeds (YouTube, Vimeo, Dailymotion)
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src") or ""
        m = _YOUTUBE_EMBED_RE.search(src)
        if m:
            _add(_normalise_youtube(src, m.group(1)), "embed", "youtube",
                 source_element="iframe")
            continue
        m = _VIMEO_EMBED_RE.search(src)
        if m:
            _add(_normalise_vimeo(m.group(1)), "embed", "vimeo",
                 source_element="iframe")
            continue
        m = _DAILYMOTION_EMBED_RE.search(src)
        if m:
            _add(f"https://www.dailymotion.com/video/{m.group(1)}", "embed", "dailymotion",
                 source_element="iframe")
            continue
        # Check additional platform embeds
        for pattern, fmt, canonical in _PLATFORM_EMBEDS:
            m = pattern.search(str(src))
            if m:
                if canonical and m.lastindex and m.lastindex >= 1:
                    url = canonical.format(id=m.group(1))
                else:
                    url = str(src) if not canonical else str(src)
                _add(url, "embed", fmt, source_element="iframe")
                break

    # 2b. Twitter/X player cards (meta tags)
    for meta in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:player")}):
        name = meta.get("name", "")
        content = meta.get("content")
        if content:
            content = str(content)
            if name == "twitter:player:stream":
                _add(urljoin(base_url, content), "direct", _guess_format(content),
                     source_element="meta[twitter:player:stream]")
            elif name == "twitter:player":
                _add(urljoin(base_url, content), "embed", "twitter",
                     source_element="meta[twitter:player]")

    # 2c. Wistia script embeds (<script src="fast.wistia.com/embed/medias/ID.jsonp">)
    for script in soup.find_all("script", src=re.compile(r"fast\.wistia\.(?:com|net)/embed/medias/")):
        src = str(script.get("src", ""))
        m = re.search(r"medias/([A-Za-z0-9]+)", src)
        if m:
            _add(f"https://fast.wistia.net/embed/iframe/{m.group(1)}", "embed", "wistia",
                 source_element="script[wistia]")

    # 2d. Brightcove <video-js> elements
    for vjs in soup.find_all("video-js"):
        video_id = vjs.get("data-video-id")
        account = vjs.get("data-account")
        if video_id and account:
            _add(f"https://players.brightcove.net/{account}/default_default/index.html?videoId={video_id}",
                 "embed", "brightcove", source_element="video-js")

    # 3. <a href> links to video files
    for a in soup.find_all("a", href=True):
        href = a["href"]
        lower = href.lower().split("?")[0]
        if any(lower.endswith(ext) for ext in _VIDEO_EXTENSIONS):
            abs_url = urljoin(base_url, href)
            _add(abs_url, "direct", _guess_format(abs_url), source_element="a")

    # 4. data-src, data-video-url, data-video-id attributes
    for attr in _DATA_ATTRS:
        for el in soup.find_all(attrs={attr: True}):
            val = el[attr]
            if val.startswith("http") or val.startswith("/"):
                abs_url = urljoin(base_url, val)
                if _guess_format(abs_url) != "unknown":
                    _add(abs_url, "direct", _guess_format(abs_url),
                         source_element=f"{el.name}[{attr}]")

    # 5. OpenGraph video tags
    for meta in soup.find_all("meta", attrs={"property": re.compile(r"^og:video(:url)?$")}):
        content = meta.get("content")
        if content:
            abs_url = urljoin(base_url, content)
            _add(abs_url, "og", _guess_format(abs_url), source_element="meta[og:video]")

    # 6. JSON-LD VideoObject
    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string
        if not text:
            continue
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "VideoObject":
                for key in ("contentUrl", "embedUrl"):
                    url = item.get(key)
                    if url:
                        abs_url = urljoin(base_url, url)
                        _add(abs_url, "jsonld", _guess_format(abs_url),
                             title=item.get("name"),
                             thumbnail=item.get("thumbnailUrl"),
                             duration=item.get("duration"),
                             source_element="script[ld+json]")

    # 7. Inline <script> content — regex for video URLs
    for script in soup.find_all("script"):
        if script.get("type") == "application/ld+json":
            continue  # already handled
        text = script.string
        if not text:
            continue
        for m in _SCRIPT_VIDEO_RE.finditer(text):
            abs_url = m.group(0)
            _add(abs_url, "script", _guess_format(abs_url), source_element="script")

    # 8. CSS inline style background: url(...) with video extensions
    for el in soup.find_all(style=True):
        style = str(el.get("style", ""))
        for m in re.finditer(r'url\(["\']?([^"\')\s]+\.(?:mp4|webm|m3u8|mpd))["\']?\)', style, re.IGNORECASE):
            abs_url = urljoin(base_url, m.group(1))
            _add(abs_url, "direct", _guess_format(abs_url), source_element="style[background]")

    # v0.25.0 P3.2: yt-dlp enrichment pass — resolve provider-specific URLs
    # (DVIDS, YouTube, etc.) that DOM scraping found but couldn't unwrap.
    if use_yt_dlp:
        candidate_urls = [r.url for r in results if is_yt_dlp_candidate(r.url)]
        if candidate_urls:
            for vr in resolve_via_yt_dlp(candidate_urls):
                if vr.url not in seen:
                    seen.add(vr.url)
                    results.append(vr)

    # Sort: page first, then direct, then embed, then others
    type_order = {"page": 0, "direct": 1, "embed": 2, "og": 3, "jsonld": 4, "script": 5, "yt-dlp": 7}
    results.sort(key=lambda r: type_order.get(r.type, 6))

    return results

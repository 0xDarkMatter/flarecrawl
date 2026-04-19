"""Video URL discovery from web pages."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup


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

# Embed URL patterns
_YOUTUBE_EMBED_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]+)"
)
_VIMEO_EMBED_RE = re.compile(
    r"https?://player\.vimeo\.com/video/(\d+)"
)
_DAILYMOTION_EMBED_RE = re.compile(
    r"https?://(?:www\.)?dailymotion\.com/embed/video/([A-Za-z0-9]+)"
)

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


def extract_videos(html: str, base_url: str) -> list[VideoResult]:
    """Extract video URLs from HTML.

    Checks <video>, <iframe> embeds, <a> links, data-* attributes,
    OpenGraph tags, JSON-LD VideoObjects, and inline script URLs.
    Returns deduplicated results sorted by type.
    """
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    results: list[VideoResult] = []

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

    # Sort: direct first, then embed, then others
    type_order = {"direct": 0, "embed": 1, "og": 2, "jsonld": 3, "script": 4}
    results.sort(key=lambda r: type_order.get(r.type, 5))

    return results

"""Content-type aware downloading for Flarecrawl.

Handles binary file downloads, content type detection, and session
building for authenticated fetches that bypass CF Browser Rendering.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import httpx


@dataclass
class ContentInfo:
    """Result of a HEAD probe for content type."""
    content_type: str
    size: int | None
    filename: str | None
    is_binary: bool
    is_json: bool


@dataclass
class DownloadResult:
    """Result of a binary download."""
    path: Path
    content_type: str
    size: int
    elapsed: float
    filename: str


_TEXT_TYPES = {"text/", "application/json", "application/xml", "application/xhtml+xml",
               "application/javascript", "application/ecmascript", "application/x-yaml",
               "application/yaml", "application/toml"}

_BINARY_EXTENSIONS = {
    ".zip", ".gz", ".tar", ".bz2", ".xz", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".mp3", ".mp4", ".wav", ".avi", ".mkv", ".mov",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".exe", ".msi", ".dmg", ".deb", ".rpm",
    ".whl", ".egg",
}


def _is_binary_content_type(ct: str) -> bool:
    """Determine if a content-type is binary."""
    ct_lower = ct.lower().split(";")[0].strip()
    for text_prefix in _TEXT_TYPES:
        if ct_lower.startswith(text_prefix):
            return False
    if ct_lower.startswith("application/") and ct_lower not in _TEXT_TYPES:
        return True
    if ct_lower.startswith(("image/", "audio/", "video/", "font/")):
        return True
    return False


def _parse_content_disposition(header: str | None) -> str | None:
    """Extract filename from Content-Disposition header."""
    if not header:
        return None
    match = re.search(r"filename\*?=['\"]?(?:UTF-8'')?([^'\";\s]+)", header, re.IGNORECASE)
    if match:
        return unquote(match.group(1))
    return None


def _filename_from_url(url: str) -> str:
    """Derive filename from URL path."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path:
        name = path.rsplit("/", 1)[-1]
        if "." in name:
            return unquote(name)
    return "download"


def detect_content_type(url: str, session: httpx.Client | None = None,
                        headers: dict | None = None) -> ContentInfo:
    """HEAD request to detect content type, size, and filename."""
    client = session or httpx.Client(timeout=15, follow_redirects=True)
    close = session is None
    try:
        resp = client.head(url, headers=headers or {})
        ct = resp.headers.get("content-type", "application/octet-stream")
        ct_clean = ct.split(";")[0].strip()
        size_str = resp.headers.get("content-length")
        size = int(size_str) if size_str and size_str.isdigit() else None
        filename = _parse_content_disposition(resp.headers.get("content-disposition"))
        if not filename:
            filename = _filename_from_url(url)
        return ContentInfo(
            content_type=ct_clean,
            size=size,
            filename=filename,
            is_binary=_is_binary_content_type(ct_clean),
            is_json="json" in ct_clean.lower(),
        )
    finally:
        if close:
            client.close()


def download_binary(url: str, session: httpx.Client, output_path: Path,
                    progress_callback: Callable[[int], None] | None = None) -> DownloadResult:
    """Stream binary download with chunked writing."""
    start = time.time()
    total = 0
    with session.stream("GET", url) as resp:
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
        filename = _parse_content_disposition(resp.headers.get("content-disposition"))
        if not filename:
            filename = _filename_from_url(url)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                f.write(chunk)
                total += len(chunk)
                if progress_callback:
                    progress_callback(total)
    elapsed = time.time() - start
    return DownloadResult(
        path=output_path,
        content_type=ct,
        size=total,
        elapsed=round(elapsed, 2),
        filename=filename,
    )


def build_session(cookies: list[dict] | None = None,
                  auth: tuple[str, str] | None = None,
                  headers: dict | None = None,
                  proxy: str | None = None) -> httpx.Client:
    """Build configured httpx client from cookie/auth/header/proxy options."""
    client = httpx.Client(
        timeout=120,
        follow_redirects=True,
        proxy=proxy,
    )
    if headers:
        client.headers.update(headers)
    if auth:
        client.auth = auth
    if cookies:
        from .cookies import cookies_to_httpx
        client.cookies = cookies_to_httpx(cookies)
    return client

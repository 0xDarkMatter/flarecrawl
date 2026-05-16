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


@dataclass(slots=True)
class ContentInfo:
    """Result of a HEAD probe for content type."""
    content_type: str
    size: int | None
    filename: str | None
    is_binary: bool
    is_json: bool
    is_html: bool


@dataclass(slots=True)
class DownloadResult:
    """Result of a binary download."""
    path: Path
    content_type: str
    size: int
    elapsed: float
    filename: str


_TEXT_TYPES = {"text/", "application/json", "application/xml", "application/xhtml+xml",
               "application/javascript", "application/ecmascript", "application/x-yaml",
               "application/yaml", "application/toml",
               "application/x-ndjson", "application/x-jsonlines", "application/jsonlines"}

_BINARY_EXTENSIONS = {
    ".zip", ".gz", ".tar", ".bz2", ".xz", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".mp3", ".mp4", ".wav", ".avi", ".mkv", ".mov",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".exe", ".msi", ".dmg", ".deb", ".rpm",
    ".whl", ".egg",
}


def _is_html_content_type(ct: str) -> bool:
    """True only for HTML/XHTML content types."""
    ct_lower = ct.lower().split(";")[0].strip()
    return ct_lower in ("text/html", "application/xhtml+xml") or ct_lower.startswith("text/html")


def _is_binary_content_type(ct: str) -> bool:
    """Determine if a content-type is binary."""
    ct_lower = ct.lower().split(";")[0].strip()
    for text_prefix in _TEXT_TYPES:
        if ct_lower.startswith(text_prefix):
            return False
    # RFC 6839: structured syntax suffixes — application/*+json and application/*+xml are text
    if ct_lower.endswith("+json") or ct_lower.endswith("+xml"):
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
                        headers: dict | None = None,
                        stealth: bool = False,
                        impersonate: str = "chrome131") -> ContentInfo:
    """HEAD request to detect content type, size, and filename.

    If ``stealth=True``, uses curl_cffi with browser TLS impersonation —
    needed for sites that fingerprint TLS handshakes (war.gov-class).
    """
    if stealth:
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            stealth = False  # fall through to httpx

    if stealth:
        # curl_cffi HEAD probe
        try:
            with cffi_requests.Session(impersonate=impersonate, timeout=15) as s:  # noqa
                if headers:
                    s.headers.update(headers)
                resp = s.head(url, allow_redirects=True)
                ct = resp.headers.get("content-type", "application/octet-stream")
                ct_clean = ct.split(";")[0].strip()
                size_str = resp.headers.get("content-length")
                size = int(size_str) if size_str and str(size_str).isdigit() else None
                filename = _parse_content_disposition(resp.headers.get("content-disposition"))
                if not filename:
                    filename = _filename_from_url(url)
                return ContentInfo(
                    content_type=ct_clean,
                    size=size,
                    filename=filename,
                    is_binary=_is_binary_content_type(ct_clean) or _filename_looks_binary(filename),
                    is_json="json" in ct_clean.lower(),
                    is_html=_is_html_content_type(ct_clean),
                )
        except Exception:
            # If HEAD fails even with stealth, infer from URL extension.
            filename = _filename_from_url(url)
            looks_binary = _filename_looks_binary(filename)
            fallback_ct = "application/octet-stream" if looks_binary else "text/html"
            return ContentInfo(
                content_type=fallback_ct,
                size=None,
                filename=filename,
                is_binary=looks_binary,
                is_json=filename.lower().endswith(".json"),
                is_html=_is_html_content_type(fallback_ct),
            )

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
            is_binary=_is_binary_content_type(ct_clean) or _filename_looks_binary(filename),
            is_json="json" in ct_clean.lower(),
            is_html=_is_html_content_type(ct_clean),
        )
    finally:
        if close:
            client.close()


def _filename_looks_binary(filename: str | None) -> bool:
    """Heuristic: does the filename look like a binary file based on extension?"""
    if not filename:
        return False
    name = filename.lower()
    return any(name.endswith(ext) for ext in _BINARY_EXTENSIONS)


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


def download_binary_stealth(
    url: str,
    output_path: Path,
    *,
    cookies: list[dict] | None = None,
    headers: dict | None = None,
    proxy: str | None = None,
    impersonate: str = "chrome131",
    progress_callback: Callable[[int], None] | None = None,
) -> DownloadResult:
    """Binary download via curl_cffi with browser TLS impersonation.

    Use this when the target server fingerprints TLS handshakes (JA3/JA4) and
    rejects stock httpx requests. curl_cffi mimics a real Chrome/Safari
    handshake, bypassing those checks. Requires the optional ``curl_cffi``
    dependency.

    Args:
        url: URL to download.
        output_path: Where to save the file.
        cookies: Optional cookie list (Playwright-shape dicts).
        headers: Extra request headers.
        proxy: Proxy URL.
        impersonate: curl_cffi browser profile (chrome131, chrome120, safari17, etc.).
        progress_callback: Per-chunk progress hook (called with cumulative bytes).

    Raises:
        ImportError: If curl_cffi isn't installed.
        RuntimeError: On non-2xx responses.
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError as exc:
        raise ImportError(
            "Stealth download requires curl_cffi. Install with: uv pip install curl_cffi"
        ) from exc

    start = time.time()
    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    session = cffi_requests.Session(
        impersonate=impersonate,
        timeout=120,
        proxies=proxies,
    )
    if headers:
        session.headers.update(headers)
    if cookies:
        from .cookies import cookies_to_httpx
        # curl_cffi accepts a dict of name->value; flatten the cookie list.
        cookie_jar = cookies_to_httpx(cookies)
        session.cookies = {c.name: c.value for c in cookie_jar.jar}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        resp = session.get(url, stream=True, allow_redirects=True)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code} fetching {url}")
        ct = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
        filename = _parse_content_disposition(resp.headers.get("content-disposition"))
        if not filename:
            filename = _filename_from_url(url)
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                f.write(chunk)
                total += len(chunk)
                if progress_callback:
                    progress_callback(total)
        # Defensive: don't leave a zero-byte file masquerading as a successful
        # download. A 200-with-empty-body usually means the server is lying or
        # the upstream returned nothing — surface as an error so callers can
        # retry / report rather than silently shipping garbage.
        if total == 0:
            try:
                output_path.unlink()
            except OSError:
                pass
            raise RuntimeError(f"empty body for {url} (HTTP {resp.status_code})")
    finally:
        session.close()

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

"""Extract cookies from local browser sessions (Chrome, Firefox).

Supports two backends:
  - rookiepy (preferred, Rust-based, faster)
  - browser-cookie3 (fallback, pure Python, wider Windows support)

Install either: uv pip install rookiepy  OR  pip install browser-cookie3
"""

from __future__ import annotations

from urllib.parse import urlparse

from .client import FlareCrawlError

# Try rookiepy first (faster, Rust-based), fall back to browser-cookie3
_backend = None

try:
    import rookiepy
    _backend = "rookiepy"
except ImportError:
    rookiepy = None

try:
    import browser_cookie3
    if _backend is None:
        _backend = "browser_cookie3"
except ImportError:
    browser_cookie3 = None


def _require_cookie_lib() -> None:
    if _backend is None:
        raise FlareCrawlError(
            "Browser cookie extraction requires rookiepy or browser-cookie3. "
            "Install with: pip install browser-cookie3  (or: uv pip install rookiepy)",
            code="MISSING_DEPENDENCY",
        )


def grab_cookies(browser: str, url: str | None = None) -> list[dict]:
    """Extract cookies from a local browser, optionally filtered by domain."""
    _require_cookie_lib()

    browser_lower = browser.lower().strip()

    if _backend == "rookiepy":
        raw = _grab_rookiepy(browser_lower)
        cookies = _normalise_rookiepy(raw)
    else:
        raw_jar = _grab_browser_cookie3(browser_lower)
        cookies = _normalise_cookie_jar(raw_jar)

    if url:
        domain = urlparse(url).netloc.lstrip("www.")
        cookies = [c for c in cookies if _domain_matches(c.get("domain", ""), domain)]

    return cookies


def _grab_rookiepy(browser: str) -> list[dict]:
    """Extract via rookiepy."""
    funcs = {
        "chrome": rookiepy.chrome,
        "firefox": rookiepy.firefox,
        "edge": rookiepy.edge,
        "brave": rookiepy.brave,
        "opera": rookiepy.opera,
    }
    fn = funcs.get(browser)
    if not fn:
        raise FlareCrawlError(
            f"Unsupported browser: {browser}. Use: chrome, firefox, edge, brave",
            code="VALIDATION_ERROR",
        )
    return fn()


def _grab_browser_cookie3(browser: str) -> "http.cookiejar.CookieJar":
    """Extract via browser-cookie3."""
    import http.cookiejar
    funcs = {
        "chrome": browser_cookie3.chrome,
        "firefox": browser_cookie3.firefox,
        "edge": browser_cookie3.edge,
        "opera": browser_cookie3.opera,
    }
    fn = funcs.get(browser)
    if not fn:
        raise FlareCrawlError(
            f"Unsupported browser: {browser}. Use: chrome, firefox, edge",
            code="VALIDATION_ERROR",
        )
    try:
        return fn()
    except Exception as e:
        raise FlareCrawlError(f"Failed to read {browser} cookies: {e}", code="COOKIE_ERROR") from e


def _normalise_rookiepy(raw: list[dict]) -> list[dict]:
    """Convert rookiepy output to flarecrawl cookie format."""
    result = []
    for c in raw:
        entry = {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
        }
        if "expires" in c:
            entry["expires"] = c["expires"]
        if "secure" in c:
            entry["secure"] = c["secure"]
        if "httponly" in c or "httpOnly" in c:
            entry["httpOnly"] = c.get("httpOnly", c.get("httponly", False))
        result.append(entry)
    return result


def _normalise_cookie_jar(jar) -> list[dict]:
    """Convert http.cookiejar.CookieJar to flarecrawl cookie format."""
    result = []
    for c in jar:
        entry = {
            "name": c.name,
            "value": c.value or "",
            "domain": c.domain or "",
            "path": c.path or "/",
        }
        if c.expires:
            entry["expires"] = c.expires
        if c.secure:
            entry["secure"] = True
        result.append(entry)
    return result


def _domain_matches(cookie_domain: str, target_domain: str) -> bool:
    """Check if a cookie domain matches the target."""
    cd = cookie_domain.lstrip(".")
    td = target_domain.lstrip(".")
    return td == cd or td.endswith(f".{cd}") or cd.endswith(f".{td}")

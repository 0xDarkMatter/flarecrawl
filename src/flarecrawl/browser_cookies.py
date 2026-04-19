"""Extract cookies from local browser sessions (Chrome, Firefox)."""

from __future__ import annotations

from urllib.parse import urlparse

from .client import FlareCrawlError

try:
    import rookiepy
except ImportError:
    rookiepy = None


def _require_rookiepy() -> None:
    if rookiepy is None:
        raise FlareCrawlError(
            "Browser cookie extraction requires rookiepy. Install with: uv pip install rookiepy",
            code="MISSING_DEPENDENCY",
        )


def grab_cookies(browser: str, url: str | None = None) -> list[dict]:
    """Extract cookies from a local browser, optionally filtered by domain."""
    _require_rookiepy()

    browser_lower = browser.lower().strip()
    if browser_lower == "chrome":
        raw = rookiepy.chrome()
    elif browser_lower == "firefox":
        raw = rookiepy.firefox()
    elif browser_lower == "edge":
        raw = rookiepy.edge()
    elif browser_lower == "brave":
        raw = rookiepy.brave()
    elif browser_lower == "opera":
        raw = rookiepy.opera()
    else:
        raise FlareCrawlError(
            f"Unsupported browser: {browser}. Use: chrome, firefox, edge, brave",
            code="VALIDATION_ERROR",
        )

    cookies = _normalise(raw)

    if url:
        domain = urlparse(url).netloc.lstrip("www.")
        cookies = [c for c in cookies if _domain_matches(c.get("domain", ""), domain)]

    return cookies


def _normalise(raw: list[dict]) -> list[dict]:
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


def _domain_matches(cookie_domain: str, target_domain: str) -> bool:
    """Check if a cookie domain matches the target."""
    cd = cookie_domain.lstrip(".")
    td = target_domain.lstrip(".")
    return td == cd or td.endswith(f".{cd}") or cd.endswith(f".{td}")

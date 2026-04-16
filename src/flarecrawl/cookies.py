"""Cookie loading, format conversion, and validation for Flarecrawl."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import httpx


def load_cookies(path: Path) -> list[dict]:
    """Load cookies from a file, auto-detecting format.

    Supported formats:
      - Puppeteer/JSON array: [{"name": "x", "value": "y", "domain": ".example.com", ...}]
      - Chrome DevTools export: same as Puppeteer but may nest under a "cookies" key
      - Netscape/Mozilla: tab-separated text (domain, flag, path, secure, expiry, name, value)
    """
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    if text.startswith("{") or text.startswith("["):
        return _load_json_cookies(text)

    return _load_netscape_cookies(text)


def _load_json_cookies(text: str) -> list[dict]:
    """Parse JSON cookie formats (Puppeteer array or Chrome DevTools export)."""
    data = json.loads(text)
    if isinstance(data, list):
        return _normalise_cookie_list(data)
    if isinstance(data, dict):
        cookies = data.get("cookies", [])
        if isinstance(cookies, list):
            return _normalise_cookie_list(cookies)
    return []


def _normalise_cookie_list(cookies: list) -> list[dict]:
    """Ensure each cookie dict has the required keys."""
    normalised = []
    for c in cookies:
        if not isinstance(c, dict):
            continue
        if "name" not in c or "value" not in c:
            continue
        entry = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
        }
        if "expires" in c:
            entry["expires"] = c["expires"]
        if "httpOnly" in c:
            entry["httpOnly"] = c["httpOnly"]
        if "secure" in c:
            entry["secure"] = c["secure"]
        if "sameSite" in c:
            entry["sameSite"] = c["sameSite"]
        normalised.append(entry)
    return normalised


def _load_netscape_cookies(text: str) -> list[dict]:
    """Parse Netscape/Mozilla cookie format (tab-separated)."""
    cookies = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        entry = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": secure.upper() == "TRUE",
        }
        try:
            exp = int(expires)
            if exp > 0:
                entry["expires"] = exp
        except ValueError:
            pass
        cookies.append(entry)
    return cookies


def cookies_to_httpx(cookies: list[dict]) -> httpx.Cookies:
    """Convert Puppeteer-style cookie dicts to httpx.Cookies."""
    jar = httpx.Cookies()
    for c in cookies:
        jar.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
    return jar


def cookies_to_header(cookies: list[dict], domain: str) -> str:
    """Build Cookie: header string, filtering by domain match."""
    parts = []
    for c in cookies:
        cookie_domain = c.get("domain", "")
        if _domain_matches(cookie_domain, domain):
            parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)


def _domain_matches(cookie_domain: str, target_domain: str) -> bool:
    """Check if a cookie domain matches the target domain."""
    if not cookie_domain:
        return True
    cd = cookie_domain.lstrip(".")
    td = target_domain.lstrip(".")
    return td == cd or td.endswith(f".{cd}")


def validate_cookies(cookies: list[dict], url: str) -> dict:
    """Test cookies against a URL with HEAD request.

    Returns dict with valid, status_code, redirected_to keys.
    """
    parsed = urlparse(url)
    header = cookies_to_header(cookies, parsed.netloc)
    headers = {"Cookie": header} if header else {}

    try:
        with httpx.Client(follow_redirects=True, timeout=15) as client:
            resp = client.head(url, headers=headers)
            final_url = str(resp.url)
            return {
                "valid": resp.status_code < 400,
                "status_code": resp.status_code,
                "redirected_to": final_url if final_url != url else None,
            }
    except httpx.HTTPError as e:
        return {
            "valid": False,
            "status_code": None,
            "redirected_to": None,
            "error": str(e),
        }

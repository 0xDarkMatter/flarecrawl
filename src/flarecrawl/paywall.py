"""Paywall bypass cascade for Flarecrawl.

Attempts to extract full article content from paywalled sites using a
multi-tier approach before falling back to browser rendering. Each tier
is tried in order; the first to return substantial content wins.

Tiers:
  1. SSR extraction - fetch HTML, detect CSS-only paywall, extract hidden text
  2. Stealth fetch - curl_cffi with browser TLS fingerprint (bypasses DataDome)
  3. Google Referer - re-fetch with first-click-free headers
  4. archive.today - read existing snapshot (no CAPTCHA for reads)
  5. Wayback Machine - check archive.org for cached version
  6. Jina Reader - fetch r.jina.ai for clean markdown
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup

from .extract import extract_main_content, html_to_markdown

# Minimum word count to consider an extraction successful
_MIN_WORD_COUNT = 200
_MIN_WORD_COUNT_JINA = 100  # Jina often returns partial but useful content

_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_GOOGLE_REFERER_HEADERS = {
    "Referer": "https://www.google.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Cache-Control": "no-cache",
}

# Paywall CSS class patterns
_PAYWALL_CLASSES = re.compile(
    r"paywall|subscriber-only|premium-content|metered-content|"
    r"piano-offer|regwall|locked-content|gated-content",
    re.IGNORECASE,
)

# Paywall overlay selectors to remove (only UI chrome, NOT content containers)
_OVERLAY_SELECTORS = [
    "[class*='paywall-overlay']", "[class*='paywall-modal']",
    "[class*='paywall-prompt']", "[class*='paywall-banner']",
    "[class*='subscribe-prompt']", "[class*='subscribe-modal']",
    "[class*='premium-overlay']", "[class*='premium-modal']",
    "[class*='piano-offer']", "[class*='piano-modal']",
    "[class*='regwall']", "[class*='tp-modal']",
    "[class*='modal-backdrop']",
    "[id*='paywall-overlay']", "[id*='paywall-modal']",
]

# Truncation indicator phrases (checked near end of content)
_TRUNCATION_PHRASES = [
    "subscribe to read", "sign in to continue", "members only",
    "subscribe to continue", "already a subscriber", "log in to read",
    "create a free account", "start your free trial",
    "this content is for subscribers", "unlock this article",
    "premium subscribers", "to continue reading",
]

_WAYBACK_API = "https://archive.org/wayback/available"
_JINA_PREFIX = "https://r.jina.ai/"

# archive.today domains (try in order — availability varies by network/ISP)
_ARCHIVE_TODAY_DOMAINS = ["archive.ph", "archive.today", "archive.is", "archive.li"]

def _get_site_headers(url: str) -> dict:
    """Look up per-site header overrides for a URL.

    Rules are loaded from default_rules.yaml (shipped) and user
    overrides at ~/.config/flarecrawl/rules.yaml.
    """
    from .rules import get_site_headers
    return get_site_headers(url)


@dataclass(slots=True)
class PaywallResult:
    """Result of a successful paywall bypass."""

    content: str
    tier: str  # "ssr" | "referer" | "wayback" | "jina"
    elapsed: float = 0.0
    metadata: dict = field(default_factory=dict)


# ------------------------------------------------------------------
# Shared session
# ------------------------------------------------------------------


def get_paywall_session(timeout: int = 15, proxy: str | None = None) -> httpx.Client:
    """Create a reusable httpx session for paywall bypass attempts.

    Use in batch mode to avoid per-URL connection overhead.
    Caller is responsible for closing.
    """
    return httpx.Client(
        timeout=httpx.Timeout(timeout),
        http2=True,
        follow_redirects=True,
        proxy=proxy,
    )


# ------------------------------------------------------------------
# Paywall detection
# ------------------------------------------------------------------


def _detect_paywall(soup: BeautifulSoup) -> bool:
    """Check if a parsed page shows paywall signals.

    Looks for JSON-LD isAccessibleForFree, paywall CSS classes,
    and truncation indicator text.
    """
    # JSON-LD: isAccessibleForFree
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            # Handle both single objects and arrays
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    if item.get("isAccessibleForFree") is False:
                        return True
                    if str(item.get("isAccessibleForFree", "")).lower() == "false":
                        return True
        except (json.JSONDecodeError, TypeError):
            continue

    # Paywall CSS classes on any element
    for el in soup.find_all(class_=_PAYWALL_CLASSES):
        if el.name not in ("script", "style", "meta"):
            return True

    # Truncation indicator text in the page body
    body = soup.find("body")
    if body:
        text = body.get_text(separator=" ", strip=True).lower()
        # Check last 1000 chars for truncation phrases
        tail = text[-1000:] if len(text) > 1000 else text
        for phrase in _TRUNCATION_PHRASES:
            if phrase in tail:
                return True

    return False


def _has_truncation_indicators(text: str) -> bool:
    """Check if extracted text ends with paywall truncation phrases."""
    tail = text[-500:].lower() if len(text) > 500 else text.lower()
    return any(phrase in tail for phrase in _TRUNCATION_PHRASES)


# ------------------------------------------------------------------
# Hidden content extraction
# ------------------------------------------------------------------


def _extract_hidden_content(soup: BeautifulSoup) -> str | None:
    """Extract article content hidden by CSS paywall overlays.

    Removes overlay elements and inline styles that hide content,
    then extracts the main article text.
    """
    # Remove paywall overlay elements
    for selector in _OVERLAY_SELECTORS:
        for el in soup.select(selector):
            el.decompose()

    # Remove inline styles that hide content on article-like containers
    for el in soup.find_all(style=True):
        style = el.get("style", "")
        if any(prop in style.lower() for prop in [
            "display: none", "display:none",
            "overflow: hidden", "overflow:hidden",
            "max-height:", "height: 0", "height:0",
            "visibility: hidden", "visibility:hidden",
        ]):
            # Remove the hiding style, keep the element
            del el["style"]

    # Also remove any class that might trigger JS-based hiding
    for el in soup.find_all(class_=_PAYWALL_CLASSES):
        classes = el.get("class", [])
        el["class"] = [c for c in classes if not _PAYWALL_CLASSES.search(c)]

    html = str(soup)
    main_html = extract_main_content(html)
    markdown = html_to_markdown(main_html)

    word_count = len(markdown.split())
    if word_count >= _MIN_WORD_COUNT:
        return markdown
    return None


# ------------------------------------------------------------------
# Tier 1: SSR extraction
# ------------------------------------------------------------------


def _fetch_html(
    url: str,
    session: httpx.Client | None,
    headers: dict,
) -> httpx.Response | None:
    """Fetch a URL, returning the response or None on error."""
    try:
        if session:
            return session.get(url, headers=headers)
        with httpx.Client(
            timeout=httpx.Timeout(15),
            http2=True,
            follow_redirects=True,
        ) as client:
            return client.get(url, headers=headers)
    except (httpx.HTTPError, OSError):
        return None


def _try_ssr_extract(
    url: str,
    session: httpx.Client | None,
    headers: dict,
) -> PaywallResult | None:
    """Tier 1: Fetch HTML and extract content hidden by CSS paywall."""
    start = time.time()

    fetch_headers = {
        "User-Agent": headers.get("User-Agent", _CHROME_UA),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": headers.get("Accept-Language", "en-US,en;q=0.9"),
    }
    # Apply per-site header rules (Googlebot UA, cookie clearing, etc.)
    site_headers = _get_site_headers(url)
    fetch_headers.update(site_headers)
    # Pass through auth headers if present (overrides site rules)
    if "Authorization" in headers:
        fetch_headers["Authorization"] = headers["Authorization"]

    resp = _fetch_html(url, session, fetch_headers)
    if resp is None or resp.status_code >= 400:
        return None

    html = resp.text
    soup = BeautifulSoup(html, "lxml")

    # Try to extract hidden content (works for CSS-only paywalls)
    content = _extract_hidden_content(soup)
    if content and not _has_truncation_indicators(content):
        elapsed = time.time() - start
        return PaywallResult(
            content=content,
            tier="ssr",
            elapsed=round(elapsed, 2),
            metadata={"wordCount": len(content.split())},
        )

    return None


# ------------------------------------------------------------------
# Tier 2: Stealth fetch (browser TLS impersonation)
# ------------------------------------------------------------------


def _try_stealth_fetch(
    url: str,
    session: httpx.Client | None,  # unused — curl_cffi has its own session
    headers: dict,
) -> PaywallResult | None:
    """Tier 2: Fetch with real browser TLS fingerprint via curl_cffi.

    DataDome and similar bot detectors check JA3/JA4 TLS fingerprints.
    httpx uses a Python TLS stack that looks nothing like a real browser.
    curl_cffi impersonates Safari/Chrome TLS handshakes, bypassing
    fingerprint-based detection.
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return None  # curl_cffi not installed, skip tier

    start = time.time()

    # Apply per-site rules BUT exclude User-Agent — the whole point of
    # stealth fetch is to impersonate a real browser TLS fingerprint.
    # A Googlebot UA would defeat that entirely.
    site_headers = _get_site_headers(url)
    fetch_headers = {
        "Referer": "https://www.google.com/",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for k, v in site_headers.items():
        if k.lower() != "user-agent":
            fetch_headers[k] = v
    if "Authorization" in headers:
        fetch_headers["Authorization"] = headers["Authorization"]

    # Try Safari first (best DataDome bypass), fall back to Chrome
    for browser in ("safari", "chrome"):
        try:
            resp = cffi_requests.get(
                url,
                impersonate=browser,
                timeout=15,
                headers=fetch_headers,
            )
        except Exception:
            continue

        if resp.status_code >= 400:
            continue

        # Check for CAPTCHA page
        if "captcha-delivery" in resp.text or "datadome" in resp.text[:5000].lower():
            continue

        main_html = extract_main_content(resp.text)
        content = html_to_markdown(main_html)

        word_count = len(content.split())
        if word_count >= _MIN_WORD_COUNT and not _has_truncation_indicators(content):
            elapsed = time.time() - start
            return PaywallResult(
                content=content,
                tier="stealth",
                elapsed=round(elapsed, 2),
                metadata={"wordCount": word_count, "impersonate": browser},
            )

    return None


# ------------------------------------------------------------------
# Tier 3: Google Referer bypass
# ------------------------------------------------------------------


def _try_referer_bypass(
    url: str,
    session: httpx.Client | None,
    headers: dict,
) -> PaywallResult | None:
    """Tier 3: Fetch with Google Referer headers (first-click-free)."""
    start = time.time()

    fetch_headers = {
        **_GOOGLE_REFERER_HEADERS,
        "User-Agent": headers.get("User-Agent", _CHROME_UA),
    }
    # Layer per-site rules on top of Google Referer headers
    site_headers = _get_site_headers(url)
    fetch_headers.update(site_headers)
    if "Authorization" in headers:
        fetch_headers["Authorization"] = headers["Authorization"]

    resp = _fetch_html(url, session, fetch_headers)
    if resp is None or resp.status_code >= 400:
        return None

    html = resp.text
    main_html = extract_main_content(html)
    content = html_to_markdown(main_html)

    word_count = len(content.split())
    if word_count >= _MIN_WORD_COUNT and not _has_truncation_indicators(content):
        elapsed = time.time() - start
        return PaywallResult(
            content=content,
            tier="referer",
            elapsed=round(elapsed, 2),
            metadata={"wordCount": word_count},
        )

    return None


# ------------------------------------------------------------------
# Tier 4: archive.today (read existing snapshots)
# ------------------------------------------------------------------


def _try_archive_today(
    url: str,
    session: httpx.Client | None,
    headers: dict,
) -> PaywallResult | None:
    """Tier 4: Fetch cached version from archive.today.

    Tries the /newest/ endpoint which redirects to the most recent snapshot
    if one exists. Reading existing snapshots typically doesn't trigger
    CAPTCHAs — those are reserved for submitting new archives.

    Tries multiple archive.today domains since availability varies by
    network and ISP.
    """
    start = time.time()

    fetch_headers = {
        "User-Agent": headers.get("User-Agent", _CHROME_UA),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    for domain in _ARCHIVE_TODAY_DOMAINS:
        archive_url = f"https://{domain}/newest/{url}"
        try:
            if session:
                resp = session.get(archive_url, headers=fetch_headers)
            else:
                with httpx.Client(
                    timeout=httpx.Timeout(10),
                    http2=True,
                    follow_redirects=True,
                ) as client:
                    resp = client.get(archive_url, headers=fetch_headers)
        except (httpx.HTTPError, OSError):
            continue  # Try next domain

        if resp.status_code != 200:
            continue

        # Check for CAPTCHA page (short response or contains challenge)
        if len(resp.text) < 5000:
            text_lower = resp.text.lower()
            if "captcha" in text_lower or "challenge" in text_lower:
                continue

        # Extract content from the archived page
        main_html = extract_main_content(resp.text)
        content = html_to_markdown(main_html)

        # Filter out archive.today chrome/navigation
        word_count = len(content.split())
        if word_count >= _MIN_WORD_COUNT:
            elapsed = time.time() - start
            final_url = str(resp.url)
            return PaywallResult(
                content=content,
                tier="archive-today",
                elapsed=round(elapsed, 2),
                metadata={
                    "wordCount": word_count,
                    "archiveUrl": final_url,
                    "archiveDomain": domain,
                },
            )

    return None


# ------------------------------------------------------------------
# Tier 5: Wayback Machine (archive.org)
# ------------------------------------------------------------------


def _try_wayback(
    url: str,
    session: httpx.Client | None,
    headers: dict,
) -> PaywallResult | None:
    """Tier 5: Fetch from Internet Archive Wayback Machine."""
    start = time.time()

    # Check availability
    api_headers = {"User-Agent": headers.get("User-Agent", _CHROME_UA)}
    try:
        if session:
            api_resp = session.get(
                _WAYBACK_API, params={"url": url}, headers=api_headers,
            )
        else:
            with httpx.Client(timeout=httpx.Timeout(10), follow_redirects=True) as c:
                api_resp = c.get(
                    _WAYBACK_API, params={"url": url}, headers=api_headers,
                )
    except (httpx.HTTPError, OSError):
        return None

    if api_resp.status_code != 200:
        return None

    try:
        data = api_resp.json()
        snapshot = data.get("archived_snapshots", {}).get("closest", {})
        if not snapshot.get("available"):
            return None
        archive_url = snapshot["url"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None

    # Fetch the archived page
    resp = _fetch_html(archive_url, session, api_headers)
    if resp is None or resp.status_code >= 400:
        return None

    main_html = extract_main_content(resp.text)
    content = html_to_markdown(main_html)

    word_count = len(content.split())
    if word_count >= _MIN_WORD_COUNT:
        elapsed = time.time() - start
        return PaywallResult(
            content=content,
            tier="wayback",
            elapsed=round(elapsed, 2),
            metadata={"wordCount": word_count, "archiveUrl": archive_url},
        )

    return None


# ------------------------------------------------------------------
# Tier 6: Jina Reader
# ------------------------------------------------------------------


def _try_jina(
    url: str,
    session: httpx.Client | None,
    headers: dict,
) -> PaywallResult | None:
    """Tier 6: Fetch clean markdown from Jina Reader."""
    start = time.time()

    jina_url = _JINA_PREFIX + url
    jina_headers = {
        "Accept": "text/markdown",
        "User-Agent": headers.get("User-Agent", _CHROME_UA),
    }

    resp = _fetch_html(jina_url, session, jina_headers)
    if resp is None or resp.status_code >= 400:
        return None

    content = resp.text.strip()
    word_count = len(content.split())
    if word_count >= _MIN_WORD_COUNT_JINA:
        elapsed = time.time() - start
        return PaywallResult(
            content=content,
            tier="jina",
            elapsed=round(elapsed, 2),
            metadata={"wordCount": word_count},
        )

    return None


# ------------------------------------------------------------------
# Main cascade
# ------------------------------------------------------------------


def try_bypass(
    url: str,
    *,
    session: httpx.Client | None = None,
    extra_headers: dict | None = None,
    timeout: int = 15,
) -> PaywallResult | None:
    """Attempt paywall bypass using a multi-tier cascade.

    Tries each tier in order: SSR extraction, stealth fetch,
    Google Referer, archive.today, Wayback Machine, Jina Reader.
    Returns on first success.

    Args:
        url: The target URL.
        session: Optional shared httpx.Client for connection reuse.
        extra_headers: Additional headers (user auth, UA override, etc.).
        timeout: Request timeout in seconds.

    Returns:
        PaywallResult on success, None if all tiers fail.
    """
    headers = dict(extra_headers or {})
    if "User-Agent" not in headers:
        headers["User-Agent"] = _CHROME_UA

    tiers = [
        _try_ssr_extract,
        _try_stealth_fetch,
        _try_referer_bypass,
        _try_archive_today,
        _try_wayback,
        _try_jina,
    ]

    for tier_fn in tiers:
        result = tier_fn(url, session, headers)
        if result is not None:
            return result

    return None

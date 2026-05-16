"""Bot-wall / challenge detection for Flarecrawl — v0.29.0 (T4).

Every connector in a hard-target harvest ends up string-matching its own
heuristic to tell "this is a block page" from "this is real content" —
Akamai interstitials that return HTTP 200, Cloudflare 1020 hard blocks,
Imperva JS-challenges, CloudFront 403s.  HTTP status is unreliable (the
Akamai interstitial is a 200), so the matching is fragile and duplicated.

This module is the single source of truth.  ``detect_block`` is a pure
function over ``(status, headers, body)`` and returns a machine-readable
``BlockInfo``.  Callers surface it as ``meta.blocked`` so connectors stop
reinventing the heuristic.

Design notes:
  - Signatures are ordered most-specific-first; the first match wins.
  - ``terminal=True`` marks a non-bypassable wall (Cloudflare 1020).  A
    mint->replay primitive must fail fast on these rather than burn its
    re-mint budget — 1020 is keyed on the egress IP/ASN, not the session.
  - Tesla-style "SPA-404" (a 200 serving an app shell that is really a
    soft-404) is intentionally NOT auto-detected: a generic detector would
    false-positive on every single-page app.  Connectors must assert their
    own content presence for that case.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

# Cap body inspection — block pages are tiny; real pages can be huge and we
# only ever need to look near the top for a signature.
_MAX_SCAN = 65_536


@dataclass(slots=True)
class BlockInfo:
    """Machine-readable verdict for a single response.

    ``blocked=False`` is the clean case; ``vendor``/``kind`` are empty then.
    """

    blocked: bool
    vendor: str = ""        # akamai | cloudflare | imperva | cloudfront | datadome | perimeterx
    kind: str = ""          # interstitial | edge_deny | js_challenge | captcha | cf_1020_hard | rate_limited
    terminal: bool = False  # True = non-bypassable (don't waste a re-mint)
    signal: str = ""        # the substring/condition that triggered the match (debugging)

    def as_dict(self) -> dict:
        return asdict(self)


def _text(body: str | bytes | None) -> str:
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body[:_MAX_SCAN].decode("utf-8", errors="replace")
    if not isinstance(body, str):
        body = str(body)
    return body[:_MAX_SCAN]


def _lower_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def detect_block(
    status: int,
    headers: Mapping[str, str] | None,
    body: str | bytes | None,
) -> BlockInfo:
    """Classify a response as a bot-wall / challenge, or clean.

    Args:
        status: HTTP status code (use 0 if unknown — Akamai interstitials
            are HTTP 200 so status is only a weak signal).
        headers: Response headers (case-insensitive lookup is applied).
        body: Response body (str or bytes).  Only the first 64 KiB is scanned.

    Returns:
        ``BlockInfo``.  ``blocked=False`` means no wall was detected — this
        is *not* a guarantee the content is what you wanted (see module docs
        on SPA-404), only that no known challenge signature was present.
    """
    h = _lower_headers(headers)
    text = _text(body)
    low = text.lower()

    # ── Cloudflare 1020 — terminal, non-bypassable ───────────────────────
    # Keyed on egress IP/ASN+firewall rule; minting a fresh session does not
    # help.  Detect before the generic CF challenge so it wins.
    if "error code: 1020" in low or (
        "cloudflare" in low and "access denied" in low and "1020" in low
    ):
        return BlockInfo(True, "cloudflare", "cf_1020_hard", terminal=True,
                          signal="cf error code 1020")

    # ── Cloudflare managed/JS challenge (solvable) ───────────────────────
    # These markers are specific enough to stand alone.
    cf_specific = (
        "cf-browser-verification", "challenge-platform", "cf_chl_opt",
        "_cf_chl", "/cdn-cgi/challenge-platform",
    )
    # "just a moment" is the challenge <title> but is also a common English
    # phrase — only trust it alongside a Cloudflare co-signal, otherwise a
    # legit page titled "Just a moment..." would false-positive.
    cf_cosignal = ("cloudflare" in low or "cdn-cgi" in low
                   or "cf-ray" in h or "/cdn-cgi/" in low)
    if (
        h.get("cf-mitigated", "").lower() == "challenge"
        or any(m in low for m in cf_specific)
        or ("just a moment" in low and cf_cosignal)
    ):
        return BlockInfo(True, "cloudflare", "js_challenge",
                         signal="cloudflare challenge page")

    # ── Cloudflare generic edge block (1010/1015/etc, "blocked") ─────────
    if ("attention required" in low and "cloudflare" in low) or (
        "sorry, you have been blocked" in low and "cloudflare" in low
    ):
        return BlockInfo(True, "cloudflare", "edge_deny",
                         signal="cloudflare edge block")

    # ── Akamai interstitial (HTTP 200, tiny body) ────────────────────────
    # The classic "Powered and protected" / errors.edgesuite.net page that
    # returns 200 with a ~146-byte body and an embedded reference number.
    akamai_markers = (
        "powered and protected by akamai",
        "errors.edgesuite.net",
        "reference&#32;&#35;",
        "akamai reference",
    )
    if any(m in low for m in akamai_markers):
        # An edge "Access Denied" + akamai is a deny, not an interstitial.
        if "access denied" in low:
            return BlockInfo(True, "akamai", "edge_deny",
                             signal="akamai access denied")
        return BlockInfo(True, "akamai", "interstitial",
                         signal="akamai interstitial")
    # Short 200 body that is just an Akamai reference stub.
    if status in (200, 0) and len(text) <= 512 and "reference #" in low and (
        "edgesuite" in low or "akamai" in low or "/_sec/" in low
    ):
        return BlockInfo(True, "akamai", "interstitial",
                         signal="akamai short reference stub")

    # ── Imperva / Incapsula ──────────────────────────────────────────────
    imperva_markers = (
        "incapsula incident id", "_incapsula_resource", "powered by incapsula",
        "visid_incap", "/_incapsula_",
    )
    if any(m in low for m in imperva_markers):
        return BlockInfo(True, "imperva", "js_challenge",
                         signal="imperva/incapsula challenge")

    # ── DataDome ─────────────────────────────────────────────────────────
    set_cookie = h.get("set-cookie", "").lower()
    if (
        "datadome" in low
        or "geo.captcha-delivery.com" in low
        or "datadome" in set_cookie
        or "x-datadome" in h
    ):
        return BlockInfo(True, "datadome", "captcha",
                         signal="datadome captcha")

    # ── PerimeterX / HUMAN ───────────────────────────────────────────────
    px_markers = ("px-captcha", "/_px/", "perimeterx", "_pxhd", "px-cdn")
    if any(m in low for m in px_markers):
        return BlockInfo(True, "perimeterx", "captcha",
                         signal="perimeterx human challenge")

    # ── CloudFront 403 edge deny ─────────────────────────────────────────
    server = h.get("server", "").lower()
    x_cache = h.get("x-cache", "").lower()
    if status == 403 and (
        "generated by cloudfront" in low
        or "cloudfront" in server
        or "error from cloudfront" in x_cache
    ):
        return BlockInfo(True, "cloudfront", "edge_deny",
                         signal="cloudfront 403")

    # ── Generic rate limiting ────────────────────────────────────────────
    if status == 429:
        vendor = ""
        if "cloudflare" in server or "cloudflare" in low:
            vendor = "cloudflare"
        return BlockInfo(True, vendor, "rate_limited",
                         signal="http 429")

    return BlockInfo(False)

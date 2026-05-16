"""Cookie-jar freshness inspection for Flarecrawl — v0.29.0 (F3).

Minted anti-bot cookie shells (Akamai ``_abck``/``bm_*``, Cloudflare
``__cf_bm``/``cf_clearance``, Imperva ``visid_incap_*``, DataDome,
PerimeterX) are short-lived.  Today the only way to ask "is this jar still
good?" is to make a request and inspect the body — reactive, and it burns
the very budget you are trying to protect.

``inspect_jar`` answers the question offline: it classifies each cookie,
identifies the anti-bot shells, computes TTLs, and returns a single
``verdict`` so a connector can re-mint *proactively* instead of after a
block.  It is also the freshness oracle the P6 primitive (``p6.py``) uses
between replay batches.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

# Anti-bot "shell" cookies — the ones whose freshness actually gates a
# replay.  Exact names and prefixes; prefixes cover per-domain suffixed
# variants (visid_incap_123456, _pxhd, etc.).
_SHELL_EXACT: dict[str, str] = {
    "_abck": "akamai",
    "bm_sz": "akamai",
    "bm_mi": "akamai",
    "bm_sv": "akamai",
    "bm_so": "akamai",
    "bm_lso": "akamai",
    "ak_bmsc": "akamai",
    "__cf_bm": "cloudflare",
    "cf_clearance": "cloudflare",
    "__cfruid": "cloudflare",
    "datadome": "datadome",
}
_SHELL_PREFIX: list[tuple[str, str]] = [
    ("visid_incap_", "imperva"),
    ("incap_ses_", "imperva"),
    ("nlbi_", "imperva"),
    ("cf_chl_", "cloudflare"),
    ("_px", "perimeterx"),       # _px, _pxhd, _pxvid, _pxff_*
]

# A shell expiring within this many seconds is "expiring" → verdict stale.
_DEFAULT_EXPIRING_THRESHOLD = 300.0


def _classify(name: str) -> tuple[bool, str]:
    """Return (is_shell, vendor) for a cookie name."""
    if name in _SHELL_EXACT:
        return True, _SHELL_EXACT[name]
    for prefix, vendor in _SHELL_PREFIX:
        if name.startswith(prefix):
            return True, vendor
    return False, ""


def _expires_epoch(cookie: dict) -> float | None:
    """Extract an absolute expiry epoch, or None for a session cookie.

    Accepts Playwright/Puppeteer shapes: ``expires`` (epoch seconds, -1 or
    absent = session) or ``expiry``.  Non-positive / unparseable = session.
    """
    for key in ("expires", "expiry", "expirationDate"):
        if key in cookie and cookie[key] is not None:
            try:
                exp = float(cookie[key])
            except (TypeError, ValueError):
                return None
            return exp if exp > 0 else None
    return None


@dataclass(slots=True)
class CookieHealth:
    name: str
    domain: str
    expires: float | None       # epoch seconds; None = session cookie
    ttl_seconds: float | None   # None = session (dies on browser close)
    state: str                  # fresh | expiring | expired | session
    is_shell: bool
    vendor: str                 # anti-bot vendor when is_shell, else ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class JarHealth:
    verdict: str                       # fresh | stale | expired | empty
    cookie_count: int
    shell_count: int
    vendors: list[str]                 # distinct anti-bot vendors among shells
    expired_shells: list[str]
    expiring_shells: list[str]
    cookies: list[CookieHealth] = field(default_factory=list)
    checked_at: float = 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["cookies"] = [c.as_dict() if isinstance(c, CookieHealth) else c
                        for c in self.cookies]
        return d

    @property
    def ok(self) -> bool:
        """True when the jar is safe to replay without re-minting."""
        return self.verdict == "fresh"


def inspect_jar(
    cookies: list[dict],
    *,
    now: float | None = None,
    expiring_threshold: float = _DEFAULT_EXPIRING_THRESHOLD,
) -> JarHealth:
    """Classify a cookie jar and return a freshness verdict.

    Verdict logic (driven by the anti-bot *shell* cookies — ordinary
    cookies do not gate a replay):

      - ``empty``   — no cookies at all
      - ``expired`` — at least one shell cookie has passed its expiry
      - ``stale``   — at least one shell expires within ``expiring_threshold``
                      (or all shells are session-only — they die on browser
                      close and cannot be trusted across a replay)
      - ``fresh``   — shells present and none expiring; or cookies present
                      with no shells at all (nothing anti-bot to age out)

    Args:
        cookies: Playwright/Puppeteer-shape cookie dicts.
        now: Override the clock (testing).  Defaults to ``time.time()``.
        expiring_threshold: Seconds-to-expiry below which a shell counts
            as expiring.

    Returns:
        ``JarHealth``.  ``.ok`` is ``True`` only for the ``fresh`` verdict.
    """
    ts = time.time() if now is None else now

    # Be liberal in what we accept: a raw json.loads of a jar file may be a
    # Chrome DevTools export ({"cookies": [...]}) rather than a bare list.
    if isinstance(cookies, dict):
        inner = cookies.get("cookies")
        cookies = inner if isinstance(inner, list) else []
    if not isinstance(cookies, list):
        cookies = []
    # Drop any non-dict entries defensively (hand-edited / malformed jars).
    cookies = [c for c in cookies if isinstance(c, dict)]

    if not cookies:
        return JarHealth("empty", 0, 0, [], [], [], [], ts)

    healths: list[CookieHealth] = []
    shell_count = 0
    vendors: list[str] = []
    expired_shells: list[str] = []
    expiring_shells: list[str] = []
    any_shell_session = False

    for c in cookies:
        name = c.get("name", "")
        is_shell, vendor = _classify(name)
        exp = _expires_epoch(c)
        if exp is None:
            ttl: float | None = None
            state = "session"
        else:
            ttl = exp - ts
            if ttl <= 0:
                state = "expired"
            elif ttl <= expiring_threshold:
                state = "expiring"
            else:
                state = "fresh"

        if is_shell:
            shell_count += 1
            if vendor and vendor not in vendors:
                vendors.append(vendor)
            if state == "expired":
                expired_shells.append(name)
            elif state == "expiring":
                expiring_shells.append(name)
            elif state == "session":
                any_shell_session = True

        healths.append(CookieHealth(
            name=name,
            domain=c.get("domain", ""),
            expires=exp,
            ttl_seconds=ttl,
            state=state,
            is_shell=is_shell,
            vendor=vendor,
        ))

    if expired_shells:
        verdict = "expired"
    elif expiring_shells or any_shell_session:
        verdict = "stale"
    else:
        verdict = "fresh"

    return JarHealth(
        verdict=verdict,
        cookie_count=len(cookies),
        shell_count=shell_count,
        vendors=vendors,
        expired_shells=expired_shells,
        expiring_shells=expiring_shells,
        cookies=healths,
        checked_at=ts,
    )

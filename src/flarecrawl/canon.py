"""URL canonicalisation for frontier dedup (v2).

Paraphrased from the survey of Scrapy/w3lib, Heritrix SURT, and Nutch
URL normalisation (see ``docs/research/FRONTIER-COMPARISON.md``). No
code is copied from any of those projects.

Canonicalisation runs the following eight steps in order:

1. Parse via :func:`urllib.parse.urlsplit`. Reject scheme-less input.
2. Case-fold the scheme and host.
3. Strip default ports (``:80`` for http, ``:443`` for https).
4. Sort query arguments by key then value.
5. Drop query arguments whose key is in :data:`TRACKING_PARAMS`.
6. Drop query arguments with an empty value.
7. Strip the fragment.
8. Normalise percent-encoding to uppercase hex.

Example
-------
>>> canonicalize("http://Example.COM:80/a?b=2&utm_source=x&a=1#top")
'http://example.com/a?a=1&b=2'
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

__all__ = ["TRACKING_PARAMS", "canonicalize"]


#: Default deny-list of query parameters dropped during canonicalisation.
#: Callers that want a superset can build their own by unioning with
#: this set; this module exposes it as a frozenset for safety.
TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_name",
        "gclid",
        "fbclid",
        "mc_eid",
        "_ga",
        "ref",
        "source",
        "igshid",
        "mkt_tok",
        "_hsenc",
        "_hsmi",
    }
)

_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443, "ws": 80, "wss": 443}

_PERCENT_RE = re.compile(rb"%([0-9A-Fa-f]{2})")


def _upper_percent(value: str) -> str:
    """Uppercase the hex digits of every ``%HH`` escape in ``value``.

    Preserves everything else. Works on strings that may contain
    non-ASCII characters by operating on the UTF-8 byte encoding and
    decoding back as UTF-8 (percent-escapes themselves are always
    ASCII).
    """
    b = value.encode("utf-8")
    b = _PERCENT_RE.sub(lambda m: b"%" + m.group(1).upper(), b)
    return b.decode("utf-8")


def _normalise_host(host: str) -> str:
    """Lowercase the host. IDN hosts are converted to ASCII via IDNA."""
    if not host:
        return ""
    # Strip any user:pass@ prefix (urlsplit keeps it inside netloc, but
    # callers shouldn't rely on credentials being preserved).
    at_idx = host.rfind("@")
    creds = ""
    if at_idx != -1:
        creds = host[: at_idx + 1]
        host = host[at_idx + 1 :]
    # Split off port.
    if host.startswith("["):
        # IPv6 literal.
        rb = host.find("]")
        if rb == -1:
            return (creds + host).lower()
        ipv6 = host[: rb + 1].lower()
        rest = host[rb + 1 :]
        return creds + ipv6 + rest
    port = ""
    if ":" in host:
        host, _, port = host.partition(":")
        port = ":" + port
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        ascii_host = host
    return creds + ascii_host.lower() + port


def canonicalize(url: str) -> str:
    """Return a canonical form of ``url`` suitable for dedup.

    Raises
    ------
    ValueError
        If the URL has no scheme (e.g. ``"example.com/x"``).

    Example
    -------
    >>> canonicalize("HTTPS://Example.com:443/path/?b=1&a=&utm_source=x#frag")
    'https://example.com/path/?b=1'
    """
    if not isinstance(url, str):
        raise TypeError(f"canonicalize() expected str, got {type(url).__name__}")
    try:
        parts = urlsplit(url)
    except ValueError as exc:  # pragma: no cover - stdlib rarely raises here
        raise ValueError(f"cannot parse url: {url!r}") from exc

    if not parts.scheme:
        raise ValueError(f"missing scheme in url: {url!r}")

    scheme = parts.scheme.lower()

    # Host + port.
    host = _normalise_host(parts.netloc)
    # Strip default ports.
    if ":" in host and not host.endswith("]"):
        # Handle trailing port only (IPv6 literals already include ']').
        hostonly, _, port_str = host.rpartition(":")
        if hostonly and port_str.isdigit():
            default = _DEFAULT_PORTS.get(scheme)
            if default is not None and int(port_str) == default:
                host = hostonly

    # Path: percent-normalise. We do NOT collapse ``/../`` manually —
    # follow stdlib behaviour. Empty path is left empty unless there is
    # a query, in which case we don't force a ``/`` either (callers
    # rarely rely on that). Percent-escapes are uppercased.
    path = _upper_percent(parts.path)

    # Query.
    pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=False)
    kept: list[tuple[str, str]] = []
    for k, v in pairs:
        if k in TRACKING_PARAMS:
            continue
        if v == "":
            continue
        kept.append((k, v))
    kept.sort(key=lambda kv: (kv[0], kv[1]))
    query = "&".join(
        f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in kept
    )
    # Uppercase percent-escapes produced by quote() (they already are,
    # but normalise any pre-existing escaped form just in case).
    query = _upper_percent(query)

    # Strip fragment.
    fragment = ""

    # Reassemble. urlunsplit preserves the '?' only if query is
    # non-empty; same for '#'.
    return urlunsplit((scheme, host, path, query, fragment))


def _roundtrip_unquote(value: str) -> str:
    """Decode percent-escapes in ``value`` (utility for tests/debugging)."""
    return unquote(value)

"""Tests for flarecrawl.canon — URL canonicalisation."""

from __future__ import annotations

import pytest

from flarecrawl.canon import ALLOWED_SCHEMES, TRACKING_PARAMS, canonicalize


def test_golden_from_spec() -> None:
    # Canonical example from docs/research/FRONTIER-COMPARISON.md
    assert (
        canonicalize("http://Example.COM:80/a?b=2&utm_source=x&a=1#top")
        == "http://example.com/a?a=1&b=2"
    )


@pytest.mark.parametrize(
    "url,expected",
    [
        # 1. lowercase scheme
        ("HTTP://example.com/", "http://example.com/"),
        ("HTTPS://example.com/", "https://example.com/"),
        # 2. lowercase host
        ("http://ExAmPlE.com/path", "http://example.com/path"),
        # 3. drop default port http
        ("http://example.com:80/x", "http://example.com/x"),
        # 4. drop default port https
        ("https://example.com:443/x", "https://example.com/x"),
        # 5. keep non-default port
        ("http://example.com:8080/x", "http://example.com:8080/x"),
        # 6. keep non-default https port
        ("https://example.com:8443/x", "https://example.com:8443/x"),
        # 7. sort query args
        ("http://example.com/?b=2&a=1", "http://example.com/?a=1&b=2"),
        # 8. sort by key then value
        (
            "http://example.com/?a=2&a=1&b=1",
            "http://example.com/?a=1&a=2&b=1",
        ),
        # 9. drop utm_source
        ("http://example.com/?utm_source=x&a=1", "http://example.com/?a=1"),
        # 10. drop all tracking params
        (
            "http://example.com/?utm_campaign=a&gclid=b&fbclid=c&mc_eid=d",
            "http://example.com/",
        ),
        # 11. drop empty-value query args
        ("http://example.com/?a=1&b=", "http://example.com/?a=1"),
        # 12. strip fragment
        ("http://example.com/#frag", "http://example.com/"),
        # 13. strip fragment preserving query
        ("http://example.com/?a=1#frag", "http://example.com/?a=1"),
        # 14. percent-encoding uppercase
        ("http://example.com/a%2fb", "http://example.com/a%2Fb"),
        ("http://example.com/a%2Fb", "http://example.com/a%2Fb"),
        # 15. trailing slash preserved
        ("http://example.com/a/", "http://example.com/a/"),
        ("http://example.com/a", "http://example.com/a"),
        # 16. empty path preserved (no forced '/')
        ("http://example.com", "http://example.com"),
        # 17. fragments containing '?' still stripped
        (
            "http://example.com/p?a=1#x?y=2",
            "http://example.com/p?a=1",
        ),
        # 19. UTF-8 query values percent-encoded
        (
            "http://example.com/?q=caf%C3%A9",
            "http://example.com/?q=caf%C3%A9",
        ),
        # 20. duplicate keys both kept when values differ
        (
            "http://example.com/?a=1&a=2",
            "http://example.com/?a=1&a=2",
        ),
        # 21. https default port + fragment + tracking
        (
            "https://Example.com:443/x?utm_source=z&a=1#top",
            "https://example.com/x?a=1",
        ),
        # 22. IDN hosts lowercased (punycode-aware)
        # xn--nxasmq6b is a valid punycode label; we accept either
        # punycode form or lowercased original as long as it's stable.
        # (We just test stability via round-trip.)
    ],
)
def test_parametrised(url: str, expected: str) -> None:
    assert canonicalize(url) == expected


def test_idn_host_is_stable() -> None:
    # Canonicalising twice yields the same result.
    once = canonicalize("http://XN--NXASMQ6B.example/")
    twice = canonicalize(once)
    assert once == twice
    assert once.startswith("http://xn--nxasmq6b")


def test_scheme_less_raises() -> None:
    with pytest.raises(ValueError):
        canonicalize("example.com/path")


def test_non_string_raises() -> None:
    with pytest.raises(TypeError):
        canonicalize(b"http://example.com/")  # type: ignore[arg-type]


def test_idempotent() -> None:
    url = "http://Example.COM:80/a?b=2&utm_source=x&a=1#top"
    first = canonicalize(url)
    assert canonicalize(first) == first


def test_tracking_params_is_frozenset() -> None:
    assert isinstance(TRACKING_PARAMS, frozenset)
    assert "utm_source" in TRACKING_PARAMS
    assert "gclid" in TRACKING_PARAMS


def test_query_order_independent_of_input() -> None:
    a = canonicalize("http://example.com/?a=1&b=2&c=3")
    b = canonicalize("http://example.com/?c=3&b=2&a=1")
    assert a == b


def test_percent_normalisation_in_query() -> None:
    # Lower-case percent escapes in query are uppercased.
    out = canonicalize("http://example.com/?q=a%2bb")
    # parse_qsl decodes '+' to space, so we round-trip through quote.
    # The key invariant is that the output uses uppercase hex.
    assert "%" not in out or all(
        c.isupper() or not c.isalpha() for c in out.split("%")[1][:2] if c.isalpha()
    )


# ---------------------------------------------------------------------------
# Scheme allow-list (defence-in-depth)
# ---------------------------------------------------------------------------
def test_allowed_schemes_constant() -> None:
    assert "http" in ALLOWED_SCHEMES
    assert "https" in ALLOWED_SCHEMES
    assert isinstance(ALLOWED_SCHEMES, frozenset)


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,hi",
        "gopher://example.com/",
        "ftp://example.com/pub/file",
    ],
)
def test_disallowed_schemes_raise(bad_url: str) -> None:
    with pytest.raises(ValueError, match="disallowed scheme"):
        canonicalize(bad_url)


def test_uppercase_http_still_allowed() -> None:
    assert canonicalize("HTTP://Example.com/") == "http://example.com/"
    assert canonicalize("HTTPS://Example.com/") == "https://example.com/"

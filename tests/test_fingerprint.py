"""Tests for flarecrawl.fingerprint."""

from __future__ import annotations

import pytest

from flarecrawl.fingerprint import fingerprint


def test_returns_16_bytes() -> None:
    fp = fingerprint("GET", "http://example.com/")
    assert isinstance(fp, bytes)
    assert len(fp) == 16


def test_deterministic() -> None:
    a = fingerprint("GET", "http://example.com/path?q=1")
    b = fingerprint("GET", "http://example.com/path?q=1")
    assert a == b


def test_method_case_insensitive() -> None:
    assert fingerprint("GET", "http://example.com/") == fingerprint(
        "get", "http://example.com/"
    )
    assert fingerprint("GET", "http://example.com/") == fingerprint(
        "Get", "http://example.com/"
    )


def test_canonicalisation_collapses_tracking_params() -> None:
    a = fingerprint("GET", "http://Example.COM:80/a?utm_source=x&b=1")
    b = fingerprint("GET", "http://example.com/a?b=1")
    assert a == b


def test_body_sensitivity() -> None:
    a = fingerprint("POST", "http://example.com/x", b"")
    b = fingerprint("POST", "http://example.com/x", b"hello")
    c = fingerprint("POST", "http://example.com/x", b"world")
    assert a != b
    assert b != c


def test_empty_body_stable() -> None:
    a = fingerprint("GET", "http://example.com/x")
    b = fingerprint("GET", "http://example.com/x", b"")
    assert a == b


def test_get_vs_post_same_url_differ() -> None:
    a = fingerprint("GET", "http://example.com/x")
    b = fingerprint("POST", "http://example.com/x")
    assert a != b


def test_different_urls_differ() -> None:
    a = fingerprint("GET", "http://example.com/a")
    b = fingerprint("GET", "http://example.com/b")
    assert a != b


def test_accepts_bytearray_and_memoryview() -> None:
    body = b"payload"
    a = fingerprint("POST", "http://example.com/x", body)
    b = fingerprint("POST", "http://example.com/x", bytearray(body))
    c = fingerprint("POST", "http://example.com/x", memoryview(body))
    assert a == b == c


def test_type_errors() -> None:
    with pytest.raises(TypeError):
        fingerprint(b"GET", "http://example.com/")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        fingerprint("GET", b"http://example.com/")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        fingerprint("GET", "http://example.com/", "body")  # type: ignore[arg-type]


def test_scheme_less_url_raises_through_canon() -> None:
    with pytest.raises(ValueError):
        fingerprint("GET", "example.com/x")

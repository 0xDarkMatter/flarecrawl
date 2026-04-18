"""Request fingerprinting for frontier v2.

A request fingerprint is a 16-byte BLAKE2b digest keyed to the
canonicalised URL, the upper-cased HTTP method, and a BLAKE2b-16 hash
of the request body (or a zero-length hash for GET requests with no
body). Fingerprints are the primary key of the frontier table; two
requests that would hit the same resource collapse to the same row
regardless of how the caller wrote the URL.

Example
-------
>>> from flarecrawl.fingerprint import fingerprint
>>> a = fingerprint("GET", "http://Example.COM:80/a?utm_source=x&b=1")
>>> b = fingerprint("get", "http://example.com/a?b=1")
>>> a == b
True
>>> len(a)
16
"""

from __future__ import annotations

import hashlib

from .canon import canonicalize

__all__ = ["fingerprint"]

_DIGEST_SIZE = 16
_SEP = b"\x00"
# Precomputed 16-byte blake2b of the empty byte string — reused for
# every GET request whose body is missing or empty.
_EMPTY_BODY_HASH = hashlib.blake2b(b"", digest_size=_DIGEST_SIZE).digest()


def fingerprint(method: str, url: str, body: bytes = b"") -> bytes:
    """Return a 16-byte fingerprint for ``(method, canonical(url), body)``.

    The URL is canonicalised internally via
    :func:`flarecrawl.canon.canonicalize`; callers pass the raw URL.

    Parameters
    ----------
    method:
        HTTP method, case-insensitive. Normalised to uppercase.
    url:
        Raw URL. Will be canonicalised.
    body:
        Request body bytes (default empty). For GET/HEAD this is
        typically ``b""``.

    Example
    -------
    >>> fingerprint("GET", "http://example.com/a") == fingerprint(
    ...     "GET", "http://example.com/a"
    ... )
    True
    """
    if not isinstance(method, str):
        raise TypeError(f"method must be str, got {type(method).__name__}")
    if not isinstance(url, str):
        raise TypeError(f"url must be str, got {type(url).__name__}")
    if not isinstance(body, (bytes, bytearray, memoryview)):
        raise TypeError(
            f"body must be bytes-like, got {type(body).__name__}"
        )

    method_upper = method.upper().encode("ascii")
    canonical = canonicalize(url).encode("utf-8")
    if body:
        body_hash = hashlib.blake2b(bytes(body), digest_size=_DIGEST_SIZE).digest()
    else:
        body_hash = _EMPTY_BODY_HASH

    payload = method_upper + _SEP + canonical + _SEP + body_hash
    return hashlib.blake2b(payload, digest_size=_DIGEST_SIZE).digest()

"""Unit tests for jarhealth — cookie-jar freshness inspection (F3)."""

from __future__ import annotations

from flarecrawl.jarhealth import inspect_jar

NOW = 1_000_000.0


def _cookie(name, *, expires=None, domain=".example.com"):
    c = {"name": name, "value": "x", "domain": domain}
    if expires is not None:
        c["expires"] = expires
    return c


class TestVerdict:
    def test_empty_jar(self):
        h = inspect_jar([], now=NOW)
        assert h.verdict == "empty"
        assert h.cookie_count == 0
        assert h.ok is False

    def test_fresh_shells(self):
        cookies = [
            _cookie("_abck", expires=NOW + 3600),
            _cookie("bm_sz", expires=NOW + 3600),
        ]
        h = inspect_jar(cookies, now=NOW)
        assert h.verdict == "fresh"
        assert h.ok is True
        assert h.shell_count == 2
        assert "akamai" in h.vendors

    def test_expired_shell(self):
        cookies = [_cookie("_abck", expires=NOW - 10)]
        h = inspect_jar(cookies, now=NOW)
        assert h.verdict == "expired"
        assert "_abck" in h.expired_shells
        assert h.ok is False

    def test_expiring_shell_is_stale(self):
        cookies = [_cookie("cf_clearance", expires=NOW + 60)]
        h = inspect_jar(cookies, now=NOW, expiring_threshold=300)
        assert h.verdict == "stale"
        assert "cf_clearance" in h.expiring_shells

    def test_session_shell_is_stale(self):
        # Session cookie (no expiry) cannot be trusted across a replay.
        cookies = [_cookie("__cf_bm")]  # no expires
        h = inspect_jar(cookies, now=NOW)
        assert h.verdict == "stale"

    def test_non_shell_cookies_only_is_fresh(self):
        cookies = [_cookie("sessionid", expires=NOW + 3600)]
        h = inspect_jar(cookies, now=NOW)
        assert h.verdict == "fresh"
        assert h.shell_count == 0

    def test_expired_wins_over_expiring(self):
        cookies = [
            _cookie("_abck", expires=NOW - 1),
            _cookie("bm_sz", expires=NOW + 60),
        ]
        h = inspect_jar(cookies, now=NOW)
        assert h.verdict == "expired"


class TestClassification:
    def test_imperva_prefix(self):
        h = inspect_jar([_cookie("visid_incap_123456", expires=NOW + 3600)],
                        now=NOW)
        assert h.shell_count == 1
        assert "imperva" in h.vendors

    def test_perimeterx_prefix(self):
        h = inspect_jar([_cookie("_pxhd", expires=NOW + 3600)], now=NOW)
        assert "perimeterx" in h.vendors

    def test_datadome_exact(self):
        h = inspect_jar([_cookie("datadome", expires=NOW + 3600)], now=NOW)
        assert "datadome" in h.vendors

    def test_multiple_vendors_deduped(self):
        cookies = [
            _cookie("_abck", expires=NOW + 3600),
            _cookie("ak_bmsc", expires=NOW + 3600),
            _cookie("__cf_bm", expires=NOW + 3600),
        ]
        h = inspect_jar(cookies, now=NOW)
        assert sorted(h.vendors) == ["akamai", "cloudflare"]


class TestCookieHealthDetail:
    def test_ttl_computed(self):
        h = inspect_jar([_cookie("_abck", expires=NOW + 1800)], now=NOW)
        c = h.cookies[0]
        assert c.ttl_seconds == 1800
        assert c.state == "fresh"
        assert c.is_shell is True
        assert c.vendor == "akamai"

    def test_session_cookie_detail(self):
        h = inspect_jar([_cookie("plain")], now=NOW)
        c = h.cookies[0]
        assert c.expires is None
        assert c.ttl_seconds is None
        assert c.state == "session"

    def test_negative_expiry_is_session(self):
        h = inspect_jar([_cookie("x", expires=-1)], now=NOW)
        assert h.cookies[0].state == "session"

    def test_as_dict_serialisable(self):
        import json
        h = inspect_jar([_cookie("_abck", expires=NOW + 100)], now=NOW)
        json.dumps(h.as_dict())  # must not raise

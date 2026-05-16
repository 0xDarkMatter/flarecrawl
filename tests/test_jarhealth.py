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


class TestMalformedInput:
    def test_chrome_devtools_dict_shape(self):
        # {"cookies": [...]} export must be accepted, not break.
        jar = {"cookies": [_cookie("_abck", expires=NOW + 3600)]}
        h = inspect_jar(jar, now=NOW)  # type: ignore[arg-type]
        assert h.verdict == "fresh"
        assert h.shell_count == 1

    def test_non_list_non_dict_is_empty(self):
        h = inspect_jar("garbage", now=NOW)  # type: ignore[arg-type]
        assert h.verdict == "empty"

    def test_non_dict_entries_dropped(self):
        cookies = ["not-a-cookie", _cookie("_abck", expires=NOW + 3600), 42]
        h = inspect_jar(cookies, now=NOW)  # type: ignore[arg-type]
        assert h.cookie_count == 1
        assert h.verdict == "fresh"

    def test_string_epoch_expiry_parsed(self):
        h = inspect_jar([_cookie("_abck", expires="1000900")], now=NOW)
        # 1000900 > NOW(1000000) by 900s → fresh
        assert h.cookies[0].ttl_seconds == 900

    def test_iso_string_expiry_degrades_to_session(self):
        # Unparseable expiry → treated as session (can't trust) → stale.
        h = inspect_jar([_cookie("__cf_bm", expires="2026-01-01T00:00:00Z")],
                        now=NOW)
        assert h.cookies[0].state == "session"
        assert h.verdict == "stale"

    def test_expirationDate_key_accepted(self):
        # EditThisCookie / extension export uses expirationDate.
        c = {"name": "_abck", "value": "v", "domain": ".x.com",
             "expirationDate": NOW + 3600}
        h = inspect_jar([c], now=NOW)
        assert h.cookies[0].ttl_seconds == 3600

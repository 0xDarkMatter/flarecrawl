"""Unit tests for blockdetect — the bot-wall classifier (T4)."""

from __future__ import annotations

from flarecrawl.blockdetect import BlockInfo, detect_block


class TestClean:
    def test_normal_html_is_not_blocked(self):
        info = detect_block(200, {"content-type": "text/html"},
                            "<html><body><h1>Welcome</h1></body></html>")
        assert info.blocked is False
        assert info.vendor == ""
        assert info.kind == ""
        assert info.terminal is False

    def test_empty_body_not_blocked(self):
        assert detect_block(200, {}, "").blocked is False
        assert detect_block(0, None, None).blocked is False

    def test_real_json_api_not_blocked(self):
        info = detect_block(200, {"content-type": "application/json"},
                            b'{"stations":[{"id":1}]}')
        assert info.blocked is False


class TestCloudflare1020Terminal:
    def test_cf_1020_is_terminal(self):
        body = "<html><body>error code: 1020</body></html>"
        info = detect_block(403, {"server": "cloudflare"}, body)
        assert info.blocked is True
        assert info.vendor == "cloudflare"
        assert info.kind == "cf_1020_hard"
        assert info.terminal is True

    def test_cf_1020_wins_over_generic_challenge(self):
        # A 1020 page may also carry challenge-ish markers; terminal wins.
        body = "Access denied. Cloudflare. error code: 1020. challenge-platform"
        info = detect_block(403, {}, body)
        assert info.kind == "cf_1020_hard"
        assert info.terminal is True


class TestCloudflareChallenge:
    def test_just_a_moment_with_cosignal(self):
        # Real CF challenge page: title + a /cdn-cgi/ asset reference.
        body = ('<title>Just a moment...</title>'
                '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate"></script>')
        info = detect_block(503, {}, body)
        assert info.blocked is True
        assert info.vendor == "cloudflare"
        assert info.kind == "js_challenge"
        assert info.terminal is False

    def test_just_a_moment_alone_is_not_blocked(self):
        # A legit page that merely says "Just a moment..." must NOT trip.
        body = "<html><body><h1>Just a moment...</h1><p>Loading your dashboard.</p></body></html>"
        info = detect_block(200, {}, body)
        assert info.blocked is False

    def test_cf_mitigated_header(self):
        info = detect_block(403, {"cf-mitigated": "challenge"}, "<html></html>")
        assert info.vendor == "cloudflare"
        assert info.kind == "js_challenge"

    def test_cf_ray_header_is_cosignal(self):
        info = detect_block(503, {"cf-ray": "8a1b2c3d"},
                            "<title>Just a moment...</title>")
        assert info.vendor == "cloudflare"
        assert info.kind == "js_challenge"

    def test_cf_edge_block(self):
        body = "Attention Required! | Cloudflare — Sorry, you have been blocked"
        info = detect_block(403, {}, body)
        assert info.vendor == "cloudflare"
        assert info.kind == "edge_deny"


class TestAkamai:
    def test_interstitial_http_200(self):
        # The classic 200 interstitial — status lies, body is the signal.
        body = "<html><body>Powered and protected by Akamai</body></html>"
        info = detect_block(200, {}, body)
        assert info.blocked is True
        assert info.vendor == "akamai"
        assert info.kind == "interstitial"
        assert info.terminal is False

    def test_edgesuite_reference(self):
        body = "Reference #18.abcd1234 errors.edgesuite.net"
        info = detect_block(200, {}, body)
        assert info.vendor == "akamai"

    def test_akamai_access_denied_is_edge_deny(self):
        body = "Access Denied — powered and protected by akamai"
        info = detect_block(403, {}, body)
        assert info.vendor == "akamai"
        assert info.kind == "edge_deny"

    def test_short_reference_stub(self):
        body = "Reference #7.1a2b edgesuite"
        info = detect_block(200, {}, body)
        assert info.blocked is True
        assert info.vendor == "akamai"


class TestImperva:
    def test_incapsula_incident(self):
        body = "Request unsuccessful. Incapsula incident ID: 1234-5678"
        info = detect_block(403, {}, body)
        assert info.blocked is True
        assert info.vendor == "imperva"
        assert info.kind == "js_challenge"


class TestDataDome:
    def test_datadome_captcha(self):
        body = "<html>geo.captcha-delivery.com</html>"
        info = detect_block(403, {}, body)
        assert info.vendor == "datadome"
        assert info.kind == "captcha"

    def test_datadome_set_cookie(self):
        info = detect_block(403, {"set-cookie": "datadome=abc; Path=/"},
                            "<html></html>")
        assert info.vendor == "datadome"


class TestPerimeterX:
    def test_px_captcha(self):
        info = detect_block(403, {}, "<div id='px-captcha'></div>")
        assert info.vendor == "perimeterx"
        assert info.kind == "captcha"


class TestCloudFront:
    def test_cloudfront_403(self):
        body = "<html><body>Generated by cloudfront (CloudFront)</body></html>"
        info = detect_block(403, {"server": "CloudFront"}, body)
        assert info.blocked is True
        assert info.vendor == "cloudfront"
        assert info.kind == "edge_deny"

    def test_cloudfront_403_requires_403(self):
        # cloudfront server header but 200 + real content → not blocked
        info = detect_block(200, {"server": "CloudFront"},
                            "<html><body>real page</body></html>")
        assert info.blocked is False


class TestRateLimit:
    def test_429_is_rate_limited(self):
        info = detect_block(429, {}, "Too Many Requests")
        assert info.blocked is True
        assert info.kind == "rate_limited"

    def test_429_with_cloudflare(self):
        info = detect_block(429, {"server": "cloudflare"}, "rate limited")
        assert info.vendor == "cloudflare"
        assert info.kind == "rate_limited"


class TestSerialization:
    def test_as_dict_shape(self):
        d = detect_block(200, {}, "ok").as_dict()
        assert set(d) == {"blocked", "vendor", "kind", "terminal", "signal"}

    def test_bytes_body_accepted(self):
        info = detect_block(200, {}, b"Powered and protected by Akamai")
        assert info.vendor == "akamai"

    def test_header_case_insensitive(self):
        info = detect_block(403, {"CF-Mitigated": "challenge"}, "x")
        assert info.vendor == "cloudflare"

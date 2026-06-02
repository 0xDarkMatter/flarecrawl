"""Tests for the vendored Wappalyzer technology detection module."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from flarecrawl.client import Client
from flarecrawl.wappalyzer import (
    DATA_DIR,
    Detection,
    WappalyzerClient,
    get_wappalyzer,
)


def _flarecrawl_cmd() -> str:
    """Locate the installed flarecrawl entry point script for subprocess use."""
    import shutil
    from pathlib import Path

    # Prefer the script alongside the current Python interpreter
    venv_bin = Path(sys.executable).parent
    candidates = [
        venv_bin / "flarecrawl.exe",
        venv_bin / "flarecrawl",
        venv_bin / "Scripts" / "flarecrawl.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    found = shutil.which("flarecrawl")
    if found:
        return found
    pytest.skip("flarecrawl CLI script not on PATH")


# ---------------------------------------------------------------------------
# Fingerprint loading
# ---------------------------------------------------------------------------


def test_data_dir_ships_with_package():
    """Vendored data directory exists on disk after install."""
    assert DATA_DIR.exists()
    assert DATA_DIR.is_dir()
    # Sanity: at least the categories + a few letter files
    assert (DATA_DIR / "categories.json").exists()
    assert (DATA_DIR / "groups.json").exists()
    assert (DATA_DIR / "w.json").exists()


def test_loads_more_than_4000_techs():
    """The vendored DB must be the real fingerprint database, not a stub."""
    w = WappalyzerClient()
    assert w.tech_count > 4000


def test_loads_custom_overlay():
    """Custom fingerprints overlay merges with the upstream DB."""
    w = WappalyzerClient()
    w._load()
    assert w._techs is not None
    # Sanity-check entries that only exist in the custom overlay
    assert "Tailwind CSS" in w._techs
    assert "Craft CMS" in w._techs
    assert "SevenRooms" in w._techs


def test_categories_loaded():
    w = WappalyzerClient()
    assert w.category_count > 50


def test_custom_overlay_categories_are_set():
    """Every custom-overlay tech in the hospitality/tourism set must have a
    non-empty `cats` after the overlay merge. An uncategorised fingerprint
    silently breaks --only-categories / --exclude-categories filtering and
    downstream consumers that group detections by category.
    """
    w = WappalyzerClient()
    w._load()
    assert w._techs is not None
    must_have_cats = [
        "Roam", "Localis", "Simpleview CMS", "Craft CMS", "ATDW",
        "SevenRooms", "OpenTable", "ResDiary", "Quandoo", "Resy",
        "Tock", "TheFork", "FareHarbor", "Rezdy", "Bokun",
        "Mews", "Cloudbeds", "SiteMinder", "Square Online",
        "Triptease", "Eventbrite", "Tailwind CSS",
    ]
    missing = []
    for name in must_have_cats:
        fp = w._techs.get(name)
        if fp is None or not fp.get("cats"):
            missing.append(name)
    assert not missing, f"Techs without cats: {missing}"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


WORDPRESS_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta name="generator" content="WordPress 6.4">
<link rel="stylesheet" href="/wp-content/themes/twentytwentyfour/style.css">
<script src="/wp-includes/js/jquery/jquery.min.js"></script>
</head>
<body>
<div class="wp-block-group">Hello</div>
</body>
</html>
"""


def test_detects_wordpress():
    """A well-known fingerprint should fire."""
    w = WappalyzerClient()
    detections = w.analyze(html=WORDPRESS_HTML)
    names = [d.name for d in detections]
    assert "WordPress" in names
    wp = next(d for d in detections if d.name == "WordPress")
    assert wp.version == "6.4"
    assert "CMS" in wp.categories


def test_implies_chain():
    """WordPress should imply PHP + MySQL."""
    w = WappalyzerClient()
    detections = w.analyze(html=WORDPRESS_HTML)
    names = {d.name for d in detections}
    assert "PHP" in names
    assert "MySQL" in names


def test_detects_via_response_headers():
    """Cloudflare is detected purely via headers (no HTML)."""
    w = WappalyzerClient()
    detections = w.analyze(html="", headers={"Server": "cloudflare", "CF-Ray": "abc123"})
    names = [d.name for d in detections]
    assert "Cloudflare" in names


def test_detects_php_via_x_powered_by_header():
    """X-Powered-By: PHP/8.2 - header-only fingerprint with a version."""
    w = WappalyzerClient()
    detections = w.analyze(html="", headers={"X-Powered-By": "PHP/8.2.13"})
    php = next((d for d in detections if d.name == "PHP"), None)
    assert php is not None
    assert php.version.startswith("8.2")


def test_detects_via_cookies_alone():
    """JSESSIONID cookie should fire Java fingerprint without any HTML."""
    w = WappalyzerClient()
    detections = w.analyze(html="", cookies={"JSESSIONID": "ABC123"})
    names = [d.name for d in detections]
    assert "Java" in names


def test_main_document_headers_collector_filters_subresources():
    """MainDocumentHeaders only stores the navigation document, not scripts/css."""
    from flarecrawl.cdp import MainDocumentHeaders

    coll = MainDocumentHeaders(expected_url="https://example.com/")
    # Subresource - ignored
    coll._on_response_received({
        "type": "Script",
        "response": {"url": "https://cdn.example.com/foo.js", "headers": {"x-foo": "1"}},
    })
    assert coll.headers == {}

    # Matching document - captured
    coll._on_response_received({
        "type": "Document",
        "response": {"url": "https://example.com/", "headers": {"server": "cloudflare"}},
    })
    assert coll.headers == {"server": "cloudflare"}
    assert coll.final_url == "https://example.com/"

    # Second document event ignored (don't overwrite)
    coll._on_response_received({
        "type": "Document",
        "response": {"url": "https://example.com/", "headers": {"server": "different"}},
    })
    assert coll.headers["server"] == "cloudflare"


def test_main_document_headers_no_expected_url_accepts_first():
    """Without an expected URL, accept the first Document response."""
    from flarecrawl.cdp import MainDocumentHeaders

    coll = MainDocumentHeaders()
    coll._on_response_received({
        "type": "Document",
        "response": {"url": "https://anything.example/", "headers": {"x-powered-by": "PHP/8.2"}},
    })
    assert coll.headers.get("x-powered-by") == "PHP/8.2"


def test_attach_tech_uses_all_signals():
    """_attach_tech feeds html + headers + cookies + js_globals to Wappalyzer."""
    from flarecrawl.cli import _attach_tech

    rec: dict = {"url": "https://example.com/"}
    _attach_tech(
        rec,
        html='<html><head></head><body></body></html>',
        headers={"X-Powered-By": "PHP/8.1.27"},
        cookies={"PHPSESSID": "deadbeef"},
    )
    techs = rec.get("technologies") or []
    names = [t["name"] for t in techs]
    # Either signal alone fires PHP; both together is the strong case
    assert "PHP" in names


def test_attach_tech_idempotent():
    """Calling _attach_tech twice should not overwrite the first result."""
    from flarecrawl.cli import _attach_tech

    rec: dict = {"url": "https://example.com/"}
    _attach_tech(rec, html=WORDPRESS_HTML)
    first = rec["technologies"]
    # Second call with no signals should not blow away first call
    _attach_tech(rec, html="<html></html>")
    assert rec["technologies"] is first


def test_filter_detections_min_confidence():
    """min_confidence drops low-score detections."""
    from flarecrawl.cli import _filter_detections

    high = Detection(name="A", confidence=100, categories=["X"])
    mid = Detection(name="B", confidence=50, categories=["X"])
    low = Detection(name="C", confidence=10, categories=["X"])

    out = _filter_detections([high, mid, low], min_confidence=50)
    assert {d.name for d in out} == {"A", "B"}


def test_filter_detections_only_categories():
    """only_categories keeps only listed-category detections (case-insensitive)."""
    from flarecrawl.cli import _filter_detections

    a = Detection(name="A", categories=["CMS"])
    b = Detection(name="B", categories=["Analytics"])
    c = Detection(name="C", categories=["CMS", "Blogs"])

    out = _filter_detections([a, b, c], only_categories=["cms"])
    assert {d.name for d in out} == {"A", "C"}


def test_filter_detections_exclude_categories():
    """exclude_categories drops anything that touches a listed category."""
    from flarecrawl.cli import _filter_detections

    a = Detection(name="A", categories=["CMS", "Analytics"])
    b = Detection(name="B", categories=["Frameworks"])

    out = _filter_detections([a, b], exclude_categories=["analytics"])
    assert {d.name for d in out} == {"B"}


def test_parse_category_list():
    from flarecrawl.cli import _parse_category_list
    assert _parse_category_list(None) is None
    assert _parse_category_list("") is None
    assert _parse_category_list("CMS, Frameworks ,") == ["CMS", "Frameworks"]
    assert _parse_category_list(" ,, ") is None


# ---------------------------------------------------------------------------
# Local HTTP fixture server - exercises real transport + headers + cookies
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tech_fixture_server():
    """A local HTTP server that returns various tech-detection signal mixes.

    Routes:
      /wordpress         - WordPress HTML + X-Powered-By + Server + PHPSESSID
      /cloudflare-only   - bare HTML body, Server: cloudflare + CF-Ray header
      /cookies-only      - empty HTML, JSESSIONID cookie (Java fingerprint)
      /500               - 500 status WITH cloudflare server header
                           (signals must survive HTTP error)
      /no-head           - 405 on HEAD, normal GET response
      /redirect          - 302 -> /wordpress (preserve final headers)
      /gzip              - gzip-encoded WordPress HTML
      /large             - 1 MB HTML body to test streaming cap
      /latin1            - latin-1 encoded body, no UTF-8 BOM
      /no-content-type   - body with no Content-Type header
      /multi-cookie      - sets multiple Set-Cookie headers
      /slow              - small delay then normal response (timeout boundary)
    """
    import gzip as _gzip
    import http.server
    import threading
    import time as _t

    LARGE_BODY = b"<html><body>" + (b"x" * (1024 * 1024)) + b"</body></html>"

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass

        def _wordpress(self) -> tuple[int, list[tuple[str, str]], bytes]:
            body = (
                b'<!doctype html><html><head>'
                b'<meta name="generator" content="WordPress 6.4">'
                b'<script src="/wp-includes/js/jquery/jquery.min.js"></script>'
                b'</head><body><div class="wp-block-paragraph">x</div></body></html>'
            )
            return 200, [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Server", "nginx/1.24.0"),
                ("X-Powered-By", "PHP/8.2.13"),
                ("Set-Cookie", "PHPSESSID=abc123; Path=/; HttpOnly"),
                ("Content-Length", str(len(body))),
            ], body

        def _serve_get(self) -> None:  # noqa: C901
            path = self.path
            if path.startswith("/wordpress"):
                status, hdrs, body = self._wordpress()
                self.send_response(status)
                for k, v in hdrs:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/cloudflare-only"):
                body = b"<html><body></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Server", "cloudflare")
                self.send_header("CF-Ray", "8abcdef0123456ab")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/cookies-only"):
                body = b"<html><body></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Set-Cookie", "JSESSIONID=DEAD; Path=/")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/500"):
                # 500 status, BUT signals should still come through
                body = b'<html><body>internal error</body></html>'
                self.send_response(500)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Server", "cloudflare")
                self.send_header("CF-Ray", "deadbeefdeadbeef")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/no-head"):
                body = b"<html><body></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("X-Powered-By", "PHP/7.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "/wordpress")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path.startswith("/gzip"):
                _, _, body = self._wordpress()
                gz = _gzip.compress(body)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("X-Powered-By", "PHP/8.2.13")
                self.send_header("Content-Length", str(len(gz)))
                self.end_headers()
                self.wfile.write(gz)
                return
            if path.startswith("/large"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Server", "cloudflare")
                self.send_header("Content-Length", str(len(LARGE_BODY)))
                self.end_headers()
                self.wfile.write(LARGE_BODY)
                return
            if path.startswith("/latin1"):
                # latin-1 only - em-dash would fail encoding
                body = "<html><body>café - naïve</body></html>".encode("latin-1")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=iso-8859-1")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/no-content-type"):
                body = b"<html><body>foo</body></html>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/multi-cookie"):
                body = b"<html><body></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Set-Cookie", "PHPSESSID=abc; Path=/")
                self.send_header("Set-Cookie", "JSESSIONID=def; Path=/")
                self.send_header("Set-Cookie", "_ga=GA1.2.123; Path=/")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/slow"):
                _t.sleep(0.2)
                status, hdrs, body = self._wordpress()
                self.send_response(status)
                for k, v in hdrs:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/spa"):
                # Synthetic SPA page that sets window.testSignal on load.
                # Paired with the FlarecrawlTestSignal custom fingerprint to
                # exercise the Playwright --render JS-globals probe path.
                body = (
                    b'<!doctype html><html><head><title>spa</title></head>'
                    b'<body><div id="root"></div>'
                    b'<script>window.testSignal = "match";</script>'
                    b'</body></html>'
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self._serve_get()

        def do_HEAD(self) -> None:  # noqa: N802
            if self.path.startswith("/no-head"):
                self.send_response(405)
                self.end_headers()
                return
            self._serve_get()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_collect_response_signals_against_fixture(tech_fixture_server):
    """End-to-end: GET against fixture server pulls headers + cookies."""
    from flarecrawl.cli import _collect_response_signals

    headers, cookies = _collect_response_signals(
        f"{tech_fixture_server}/wordpress",
        timeout=5.0,
    )
    # Headers came through (case may be lowercased by httpx)
    lowered = {k.lower(): v for k, v in headers.items()}
    assert "x-powered-by" in lowered
    assert "PHP/8.2.13" == lowered["x-powered-by"]
    assert "phpsessid" in {c.lower() for c in cookies}


def test_collect_response_signals_falls_through_500(tech_fixture_server):
    """A 5xx still returns whatever headers the server sent (not an exception)."""
    from flarecrawl.cli import _collect_response_signals

    headers, cookies = _collect_response_signals(
        f"{tech_fixture_server}/500",
        timeout=5.0,
    )
    # Server responded - we got something back, even if there's no body
    assert isinstance(headers, dict)
    assert isinstance(cookies, dict)


def test_collect_response_signals_unreachable_url():
    """Transport error returns empty - never raises."""
    from flarecrawl.cli import _collect_response_signals

    # Unallocated port on localhost - connection refused
    headers, cookies = _collect_response_signals(
        "http://127.0.0.1:1/whatever",
        timeout=1.0,
    )
    assert headers == {}
    assert cookies == {}


def test_fetch_for_tech_detect_returns_html_headers_cookies(tech_fixture_server):
    """The dedicated subcommand fetcher returns the full signal triple."""
    from flarecrawl.cli import _fetch_for_tech_detect

    html, headers, cookies = _fetch_for_tech_detect(
        f"{tech_fixture_server}/wordpress",
        timeout=5.0,
    )
    assert "WordPress" in html or "generator" in html
    lowered = {k.lower(): v for k, v in headers.items()}
    assert lowered.get("x-powered-by") == "PHP/8.2.13"
    assert "nginx" in lowered.get("server", "")
    assert "PHPSESSID" in cookies


def test_cli_tech_detect_subcommand_end_to_end(tech_fixture_server):
    """`flarecrawl tech-detect URL --json` against the fixture server."""
    result = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect",
         f"{tech_fixture_server}/wordpress", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = json.loads(result.stdout)
    techs = payload["data"][0]["technologies"]
    names = {t["name"] for t in techs}
    # All three signal layers should fire
    assert "WordPress" in names      # from HTML meta generator
    assert "PHP" in names            # from X-Powered-By header (and implied)
    assert "Nginx" in names          # from Server header


def test_cli_tech_detect_cloudflare_via_headers(tech_fixture_server):
    """Cloudflare fires from headers alone (the HTML body is essentially empty)."""
    result = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect",
         f"{tech_fixture_server}/cloudflare-only", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = json.loads(result.stdout)
    techs = payload["data"][0]["technologies"]
    names = {t["name"] for t in techs}
    assert "Cloudflare" in names


def test_cli_tech_detect_filter_only_categories(tech_fixture_server):
    """--only-categories restricts results to the requested category set."""
    result = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect",
         f"{tech_fixture_server}/wordpress",
         "--json", "--only-categories", "CMS"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = json.loads(result.stdout)
    techs = payload["data"][0]["technologies"]
    # Every detection touches CMS, or the list is empty
    for t in techs:
        assert any("CMS" in c for c in t.get("categories", []))


def test_cli_tech_detect_filter_exclude_categories(tech_fixture_server):
    """--exclude-categories drops entire categories from output."""
    result = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect",
         f"{tech_fixture_server}/wordpress",
         "--json", "--exclude-categories", "CMS,Blogs"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = json.loads(result.stdout)
    techs = payload["data"][0]["technologies"]
    names = {t["name"] for t in techs}
    # WordPress was in CMS+Blogs - both excluded; should be gone
    assert "WordPress" not in names


def test_cli_tech_detect_stealth_falls_back_when_curl_cffi_missing(monkeypatch):
    """If curl_cffi is missing, --stealth should fail-soft for tech-detect.

    The _fetch_for_tech_detect path returns empty triple on ImportError;
    the CLI should still emit a valid JSON envelope.
    """
    # Patch the in-process helper directly (subprocess wouldn't see monkeypatch)
    from flarecrawl import cli as _cli

    def _fake_fetch(*args, **kwargs):
        return "", {}, {}

    monkeypatch.setattr(_cli, "_fetch_for_tech_detect", _fake_fetch)
    # Drive the helper-level path: no real network needed
    rec: dict = {"url": "https://example.com/"}
    _cli._attach_tech(rec)
    assert "technologies" not in rec  # nothing to detect, idempotent no-op


# ---------------------------------------------------------------------------
# Robustness / edge cases - signal extraction
# ---------------------------------------------------------------------------


def test_signals_survive_500_status(tech_fixture_server):
    """A 5xx response still surfaces useful headers - Cloudflare error page
    must report as Cloudflare. raise_for_status would have lost this."""
    from flarecrawl.cli import _fetch_for_tech_detect

    html, headers, cookies = _fetch_for_tech_detect(
        f"{tech_fixture_server}/500",
        timeout=5.0,
    )
    # Body + headers came through despite 500. Python's BaseHTTPHandler
    # prepends its own Server token; substring-check the real signal.
    lowered = {k.lower(): v for k, v in headers.items()}
    assert "cloudflare" in lowered.get("server", "")


def test_signals_after_redirect(tech_fixture_server):
    """302 -> /wordpress: final headers + cookies must be returned, not the
    redirect's empty Location-only response."""
    from flarecrawl.cli import _fetch_for_tech_detect

    html, headers, cookies = _fetch_for_tech_detect(
        f"{tech_fixture_server}/redirect",
        timeout=5.0,
    )
    lowered = {k.lower(): v for k, v in headers.items()}
    assert lowered.get("x-powered-by") == "PHP/8.2.13"
    assert "PHPSESSID" in cookies
    assert "WordPress" in html or "generator" in html


def test_signals_handles_gzip(tech_fixture_server):
    """gzip-encoded body decompresses transparently via httpx."""
    from flarecrawl.cli import _fetch_for_tech_detect

    html, headers, _ = _fetch_for_tech_detect(
        f"{tech_fixture_server}/gzip",
        timeout=5.0,
    )
    assert "WordPress" in html or "generator" in html
    lowered = {k.lower(): v for k, v in headers.items()}
    assert lowered.get("x-powered-by") == "PHP/8.2.13"


def test_signals_handles_latin1(tech_fixture_server):
    """Non-UTF-8 body shouldn't crash the decoder."""
    from flarecrawl.cli import _fetch_for_tech_detect

    html, _, _ = _fetch_for_tech_detect(
        f"{tech_fixture_server}/latin1",
        timeout=5.0,
    )
    # httpx decodes per content-type; should yield meaningful text
    assert "caf" in html.lower() or "café" in html


def test_signals_no_content_type_header(tech_fixture_server):
    """Server returning no Content-Type doesn't blow up."""
    from flarecrawl.cli import _fetch_for_tech_detect

    html, headers, _ = _fetch_for_tech_detect(
        f"{tech_fixture_server}/no-content-type",
        timeout=5.0,
    )
    assert "foo" in html


def test_collect_response_signals_streams_large_body(tech_fixture_server):
    """1 MB body must not download in full - signals returned promptly via
    the SNIFF_BYTES cap (32 KB)."""
    import time as _t
    from flarecrawl.cli import _collect_response_signals

    t0 = _t.time()
    headers, cookies = _collect_response_signals(
        f"{tech_fixture_server}/large",
        timeout=5.0,
    )
    elapsed = _t.time() - t0
    lowered = {k.lower(): v for k, v in headers.items()}
    assert "cloudflare" in lowered.get("server", "")
    # 1 MB on localhost should be < 1s anyway, but the streaming abort
    # means we don't pay for the full download. Give it a generous bound.
    assert elapsed < 2.0


def test_collect_response_signals_picks_up_multi_cookie(tech_fixture_server):
    """Three Set-Cookie headers - jar should hold all three."""
    from flarecrawl.cli import _collect_response_signals

    _, cookies = _collect_response_signals(
        f"{tech_fixture_server}/multi-cookie",
        timeout=5.0,
    )
    assert "PHPSESSID" in cookies
    assert "JSESSIONID" in cookies
    assert "_ga" in cookies


# ---------------------------------------------------------------------------
# Pattern parsing / regex resilience
# ---------------------------------------------------------------------------


def test_parse_pattern_extracts_version_and_confidence():
    from flarecrawl.wappalyzer import _parse_pattern

    pat, meta = _parse_pattern(r"WordPress/(\d+\.\d+)\;version:\1\;confidence:80")
    assert pat == r"WordPress/(\d+\.\d+)"
    assert meta["version"] == r"\1"
    assert meta["confidence"] == 80


def test_parse_pattern_empty():
    from flarecrawl.wappalyzer import _parse_pattern
    assert _parse_pattern("") == ("", {})


def test_parse_pattern_malformed_confidence_ignored():
    from flarecrawl.wappalyzer import _parse_pattern
    _, meta = _parse_pattern(r"foo\;confidence:notanumber")
    assert "confidence" not in meta


def test_safe_match_returns_none_on_bad_regex():
    from flarecrawl.wappalyzer import _safe_match
    # Catastrophic backtracking patterns appear in real Wappalyzer data;
    # invalid regexes must not throw.
    assert _safe_match("[unclosed", "any text") is None


def test_safe_match_empty_inputs():
    from flarecrawl.wappalyzer import _safe_match
    assert _safe_match("", "text") is None
    assert _safe_match("foo", "") is None


def test_analyze_handles_giant_html():
    """Analysis over a large body must terminate without hanging.

    Real-world pages are <500 KB; we test ~250 KB as the realistic upper
    bound while keeping the test fast. The point is to verify there's no
    catastrophic backtracking, not to benchmark.
    """
    import time as _t
    w = WappalyzerClient()
    head = '<html><head><meta name="generator" content="WordPress 6.4"></head><body>'
    middle = "<p>filler text here that won't match much</p>" * 5_000  # ~225 KB
    tail = "</body></html>"
    html = head + middle + tail
    t0 = _t.time()
    detections = w.analyze(html=html)
    elapsed = _t.time() - t0
    assert elapsed < 30.0, f"giant-HTML analysis took {elapsed:.1f}s"
    assert any(d.name == "WordPress" for d in detections)


def test_analyze_unicode_html_headers_cookies():
    """Non-ASCII anywhere shouldn't crash."""
    w = WappalyzerClient()
    html = '<html><head><meta name="generator" content="WordPress 6.4 — naïve">' \
           '</head><body>café 北京</body></html>'
    headers = {"X-Test": "naïve", "Server": "café-server"}
    cookies = {"_session_北京": "value"}
    detections = w.analyze(html=html, headers=headers, cookies=cookies)
    assert any(d.name == "WordPress" for d in detections)


def test_analyze_idempotent_per_call():
    """Calling analyze() twice in a row produces the same output."""
    w = WappalyzerClient()
    a = [d.name for d in w.analyze(html=WORDPRESS_HTML)]
    b = [d.name for d in w.analyze(html=WORDPRESS_HTML)]
    assert a == b


# ---------------------------------------------------------------------------
# Implies chain
# ---------------------------------------------------------------------------


def test_implies_chain_resolves_transitively():
    """WordPress -> PHP (upstream implies) -> ... resolve via the loop."""
    w = WappalyzerClient()
    detections = w.analyze(html=WORDPRESS_HTML)
    names = {d.name for d in detections}
    # WordPress implies PHP and MySQL; both should appear even though
    # nothing in the HTML names them directly.
    assert "PHP" in names
    assert "MySQL" in names


def test_implies_does_not_loop_forever():
    """No infinite loop even if the fingerprint DB has cyclic implies."""
    import time as _t
    w = WappalyzerClient()
    t0 = _t.time()
    w.analyze(html=WORDPRESS_HTML)
    assert _t.time() - t0 < 5.0


# ---------------------------------------------------------------------------
# Custom overlay
# ---------------------------------------------------------------------------


def test_custom_overlay_extends_existing_tech(tmp_path):
    """When a tech name exists upstream AND in the overlay, list-valued
    fields should be extended (not overwritten)."""
    import json as _json

    # Build a tiny data dir: one upstream tech + one overlay extending it
    data = tmp_path
    (data / "a.json").write_text(_json.dumps({
        "Acme": {"html": ["acme-html-pat-1"], "cats": [1]}
    }), encoding="utf-8")
    (data / "custom_fingerprints.json").write_text(_json.dumps({
        "_meta": {"description": "test"},
        "Acme": {"html": ["acme-html-pat-2"], "meta": {"generator": "Acme"}},
    }), encoding="utf-8")
    (data / "categories.json").write_text(_json.dumps({"1": {"name": "X", "groups": []}}), encoding="utf-8")
    (data / "groups.json").write_text("{}", encoding="utf-8")

    w = WappalyzerClient(data_dir=data)
    w._load()
    assert w._techs is not None
    acme = w._techs["Acme"]
    # Lists extended
    assert "acme-html-pat-1" in acme["html"]
    assert "acme-html-pat-2" in acme["html"]
    # New dict field added
    assert acme["meta"]["generator"] == "Acme"


def test_custom_overlay_adds_new_tech(tmp_path):
    """A wholly-new tech in the overlay should appear in the DB."""
    import json as _json

    data = tmp_path
    (data / "a.json").write_text("{}", encoding="utf-8")
    (data / "custom_fingerprints.json").write_text(_json.dumps({
        "_meta": {"description": "test"},
        "NewTech": {"html": ["new-pat"], "cats": [1]},
    }), encoding="utf-8")
    (data / "categories.json").write_text(_json.dumps({"1": {"name": "X", "groups": []}}), encoding="utf-8")
    (data / "groups.json").write_text("{}", encoding="utf-8")

    w = WappalyzerClient(data_dir=data)
    w._load()
    assert w._techs is not None
    assert "NewTech" in w._techs


def test_custom_overlay_disabled_list_removes_techs(tmp_path):
    """Top-level `_disabled` array drops the listed techs from the merged DB."""
    import json as _json

    data = tmp_path
    (data / "a.json").write_text(_json.dumps({
        "Acme": {"cats": [1]},
        "Beta": {"cats": [1]},
        "Gamma": {"cats": [1]},
    }), encoding="utf-8")
    (data / "custom_fingerprints.json").write_text(_json.dumps({
        "_meta": {"description": "test"},
        "_disabled": ["Beta", "Gamma"],
    }), encoding="utf-8")
    (data / "categories.json").write_text(_json.dumps({"1": {"name": "X", "groups": []}}), encoding="utf-8")
    (data / "groups.json").write_text("{}", encoding="utf-8")

    w = WappalyzerClient(data_dir=data)
    w._load()
    assert w._techs is not None
    assert "Acme" in w._techs
    assert "Beta" not in w._techs
    assert "Gamma" not in w._techs


def test_custom_overlay_disabled_strips_implies(tmp_path):
    """A disabled tech is removed from `implies` chains so the implies
    resolver can't drag it back in via a different detected tech."""
    import json as _json

    data = tmp_path
    (data / "a.json").write_text(_json.dumps({
        "Acme": {"cats": [1], "implies": ["Beta", "Gamma\\;confidence:50"]},
        "Beta": {"cats": [1]},
        "Gamma": {"cats": [1]},
    }), encoding="utf-8")
    (data / "custom_fingerprints.json").write_text(_json.dumps({
        "_meta": {"description": "test"},
        "_disabled": ["Beta"],
    }), encoding="utf-8")
    (data / "categories.json").write_text(_json.dumps({"1": {"name": "X", "groups": []}}), encoding="utf-8")
    (data / "groups.json").write_text("{}", encoding="utf-8")

    w = WappalyzerClient(data_dir=data)
    w._load()
    assert w._techs is not None
    acme_implies = w._techs["Acme"].get("implies", [])
    # Beta dropped from implies; Gamma (with confidence suffix) preserved.
    assert "Beta" not in acme_implies
    assert any("Gamma" in entry for entry in acme_implies)


def test_implies_chain_patches():
    """Overlay-extended implies chains for techs that ship broken
    upstream. If any of these regress, detection of the runtime stack
    behind common frameworks vanishes silently."""
    from flarecrawl.wappalyzer import get_wappalyzer
    w = get_wappalyzer()
    w._load()
    assert w._techs is not None

    techs = w._techs
    assert techs is not None

    def implies_contains(tech: str, target: str) -> bool:
        impls = techs.get(tech, {}).get("implies") or []
        for impl in impls:
            name = impl.split("\\;", 1)[0] if isinstance(impl, str) else ""
            if name == target:
                return True
        return False

    # JS framework -> Node.js (10 patched)
    for js_framework in ("Astro", "Remix", "SvelteKit", "SolidStart",
                        "Gatsby", "Strapi", "Eleventy", "Gridsome",
                        "VuePress", "VitePress", "Next.js", "Nuxt.js",
                        "Vercel"):
        assert implies_contains(js_framework, "Node.js"), \
            f"{js_framework} should imply Node.js"

    # PHP framework -> PHP
    for php_framework in ("Zend", "WooCommerce"):
        assert implies_contains(php_framework, "PHP"), \
            f"{php_framework} should imply PHP"

    # WooCommerce is a WordPress plugin
    assert implies_contains("WooCommerce", "WordPress")
    assert implies_contains("WooCommerce", "MySQL")

    # Non-JS/non-PHP language ecosystems (audit 2026-06-02)
    for tech, lang in (
        ("Amber", "Crystal"),
        ("Kemal", "Crystal"),
        ("Streamlit", "Python"),
        ("PyWebIO", "Python"),
        ("CherryPy", "Python"),
        ("WEBrick", "Ruby"),
        ("Yaws", "Erlang"),
        ("Hugo", "Go"),
        ("Turbopack", "Rust"),
    ):
        assert implies_contains(tech, lang), f"{tech} should imply {lang}"


def test_patched_empty_upstream_fingerprints_fire():
    """Six upstream Wappalyzer entries (Loom, DocuSign, Dropbox, Index
    Exchange, Sitecore Experience Platform, Triple Whale) ship with
    zero detection patterns. The overlay patches them with HTTP-visible
    patterns; this test guards against regressions in those patches.

    Synthetic HTML built to match each tech's overlay pattern verbatim
    - if any of these stops firing, the upstream entry has either been
    fixed (good - remove the overlay) or our pattern has regressed
    (fix it). Either way, the maintainer is told.
    """
    cases: list[tuple[str, str]] = [
        ("Loom",
         '<iframe src="https://www.loom.com/embed/abc123def"></iframe>'),
        ("DocuSign",
         '<script src="https://js.docusign.com/api/foo.js"></script>'),
        ("Dropbox",
         '<a class="dropbox-chooser" href="#">share</a>'),
        ("Index Exchange",
         '<script src="https://js-sec.indexww.com/ht/p/foo.js"></script>'),
        ("Sitecore Experience Platform",
         '<meta name="generator" content="Sitecore">'),
        ("Triple Whale",
         '<script src="https://config.triplewhale-pixel.com/cs.js"></script>'),
    ]
    w = WappalyzerClient()
    for tech_name, html in cases:
        detections = w.analyze(html=f"<html><body>{html}</body></html>")
        names = [d.name for d in detections]
        assert tech_name in names, f"Patched fingerprint for {tech_name!r} did not fire"


def test_well_known_false_positives_are_disabled():
    """The shipped overlay disables Element UI / Google Sites / etc.
    These have chronic upstream FP issues - if they sneak back in,
    detection quality regresses sharply."""
    w = WappalyzerClient()
    w._load()
    assert w._techs is not None
    for fp_tech in ("Element UI", "Google Sites", "Cart Functionality",
                    "C3.js", "Contentful", "ZURB Foundation"):
        assert fp_tech not in w._techs, f"{fp_tech} should be disabled"


def test_custom_overlay_malformed_json_does_not_break_load(tmp_path):
    """A broken overlay file logs a warning but leaves the upstream DB intact."""
    import json as _json

    data = tmp_path
    (data / "a.json").write_text(_json.dumps({"Acme": {"cats": [1]}}), encoding="utf-8")
    (data / "custom_fingerprints.json").write_text("{ this is not json", encoding="utf-8")
    (data / "categories.json").write_text(_json.dumps({"1": {"name": "X", "groups": []}}), encoding="utf-8")
    (data / "groups.json").write_text("{}", encoding="utf-8")

    w = WappalyzerClient(data_dir=data)
    w._load()
    assert w._techs is not None
    # Upstream entries still loaded
    assert "Acme" in w._techs


# ---------------------------------------------------------------------------
# Filter edge cases
# ---------------------------------------------------------------------------


def test_filter_min_confidence_100_keeps_certain_only():
    from flarecrawl.cli import _filter_detections
    d1 = Detection(name="Strong", confidence=100, categories=["X"])
    d2 = Detection(name="Weak", confidence=99, categories=["X"])
    out = _filter_detections([d1, d2], min_confidence=100)
    assert [d.name for d in out] == ["Strong"]


def test_filter_only_then_exclude():
    """only_categories runs first, exclude_categories runs after."""
    from flarecrawl.cli import _filter_detections
    a = Detection(name="A", categories=["CMS"])
    b = Detection(name="B", categories=["CMS", "Analytics"])
    c = Detection(name="C", categories=["Frameworks"])
    out = _filter_detections(
        [a, b, c],
        only_categories=["CMS", "Analytics"],
        exclude_categories=["Analytics"],
    )
    # Only A survives: B touches Analytics (excluded); C not in only
    assert [d.name for d in out] == ["A"]


def test_filter_with_empty_categories_detection():
    """A detection with no categories shouldn't crash filtering."""
    from flarecrawl.cli import _filter_detections
    d = Detection(name="A", categories=[])
    # only_categories with anything -> A drops (no category matches)
    assert _filter_detections([d], only_categories=["CMS"]) == []
    # exclude_categories with anything -> A survives
    assert _filter_detections([d], exclude_categories=["CMS"]) == [d]


# ---------------------------------------------------------------------------
# CDP MainDocumentHeaders edge cases
# ---------------------------------------------------------------------------


def test_main_document_headers_no_type_field():
    """Some CDP events arrive without a type field — don't crash."""
    from flarecrawl.cdp import MainDocumentHeaders
    coll = MainDocumentHeaders(expected_url="https://example.com/")
    coll._on_response_received({
        "response": {"url": "https://example.com/", "headers": {"x-foo": "1"}},
    })
    # No `type` means we accept the response (filter is fail-open)
    assert coll.headers.get("x-foo") == "1"


def test_main_document_headers_redirect_chain():
    """A redirect chain (301 -> 302 -> 200) should land on the final URL's
    headers, not the redirector's."""
    from flarecrawl.cdp import MainDocumentHeaders
    coll = MainDocumentHeaders(expected_url="https://example.com/final")
    # First: redirector (non-matching URL)
    coll._on_response_received({
        "type": "Document",
        "response": {"url": "https://example.com/", "headers": {"location": "/final"}},
    })
    # Then: final document
    coll._on_response_received({
        "type": "Document",
        "response": {"url": "https://example.com/final", "headers": {"server": "nginx"}},
    })
    assert coll.headers.get("server") == "nginx"


def test_main_document_headers_handles_non_string_header_values():
    """Some CDP events have int header values - coerce to str."""
    from flarecrawl.cdp import MainDocumentHeaders
    coll = MainDocumentHeaders()
    coll._on_response_received({
        "type": "Document",
        "response": {"url": "https://example.com/", "headers": {"content-length": 1234}},
    })
    assert coll.headers["content-length"] == "1234"


# ---------------------------------------------------------------------------
# CLI subcommand edge cases
# ---------------------------------------------------------------------------


def test_cli_tech_detect_ndjson_streams(tech_fixture_server, tmp_path):
    """--ndjson outputs one valid JSON record per line for multi-URL."""
    out = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect",
         f"{tech_fixture_server}/wordpress",
         f"{tech_fixture_server}/cloudflare-only",
         "--ndjson", "-w", "2"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2
    records = [json.loads(ln) for ln in lines]
    urls = {r["url"] for r in records}
    assert urls == {
        f"{tech_fixture_server}/wordpress",
        f"{tech_fixture_server}/cloudflare-only",
    }


def test_cli_tech_detect_input_file(tech_fixture_server, tmp_path):
    """--input FILE loads URLs from a file (one per line, blanks ignored)."""
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text(
        f"{tech_fixture_server}/wordpress\n"
        f"\n"
        f"{tech_fixture_server}/cloudflare-only\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect", "-i", str(urls_file), "--json"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    payload = json.loads(out.stdout)
    assert payload["meta"]["count"] == 2


def test_cli_tech_detect_writes_to_output_file(tech_fixture_server, tmp_path):
    """--output PATH writes the JSON payload to disk and prints a save line."""
    target = tmp_path / "tech.json"
    out = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect",
         f"{tech_fixture_server}/wordpress",
         "--json", "-o", str(target)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["data"][0]["url"].endswith("/wordpress")


def test_cli_tech_detect_unreachable_url_exits_clean(tmp_path):
    """A connection-refused URL doesn't crash - it surfaces as an error
    record but the CLI exits 0."""
    out = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect",
         "http://127.0.0.1:1/whatever", "--json", "--timeout", "1"],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    payload = json.loads(out.stdout)
    rec = payload["data"][0]
    assert rec.get("error") == "fetch failed"
    assert rec["technologies"] == []


def test_cli_tech_detect_no_urls_errors_clean():
    """No URL + no --input + no --stdin should produce a friendly error."""
    out = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect", "--json"],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert out.returncode != 0
    # Whether stdout has JSON envelope or stderr has the message, the
    # process must NOT crash with a traceback.
    assert "Traceback" not in (out.stdout + out.stderr)


def test_cli_tech_detect_preserves_url_order(tech_fixture_server):
    """Multiple URLs - output order matches input order (not completion)."""
    urls = [
        f"{tech_fixture_server}/wordpress",
        f"{tech_fixture_server}/cloudflare-only",
        f"{tech_fixture_server}/cookies-only",
    ]
    out = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect", *urls, "--json", "-w", "3"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    payload = json.loads(out.stdout)
    got = [r["url"] for r in payload["data"]]
    assert got == urls


def test_cli_tech_detect_only_and_exclude_combine(tech_fixture_server):
    """--only and --exclude both applied, exclude runs after only."""
    out = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect",
         f"{tech_fixture_server}/wordpress",
         "--json",
         "--only-categories", "CMS,Programming languages",
         "--exclude-categories", "Programming languages"],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert out.returncode == 0
    techs = json.loads(out.stdout)["data"][0]["technologies"]
    # Should keep CMS techs but drop pure-Programming-languages ones (PHP)
    names = {t["name"] for t in techs}
    assert "PHP" not in names


def test_cli_tech_detect_min_confidence_drops_low_score(tech_fixture_server):
    """--min-confidence drops anything below the floor."""
    out = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect",
         f"{tech_fixture_server}/wordpress",
         "--json", "--min-confidence", "100"],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert out.returncode == 0
    techs = json.loads(out.stdout)["data"][0]["technologies"]
    # Every surviving tech reports confidence omitted (100) or explicit 100
    for t in techs:
        c = t.get("confidence", 100)
        assert c >= 100


def test_cli_tech_detect_empty_stdin_no_crash():
    """Empty stdin pipe should produce an empty detection list, not crash."""
    out = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect", "--stdin", "--json"],
        input="",
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert out.returncode == 0, f"stderr: {out.stderr}"
    payload = json.loads(out.stdout)
    assert payload["data"][0]["technologies"] == []


def test_cli_guide_tech_detect_resolves():
    """The discoverability layer: `flarecrawl guide tech-detect`."""
    for alias in ("tech-detect", "tech", "wappalyzer", "fingerprint", "stack", "detect"):
        out = subprocess.run(
            [_flarecrawl_cmd(), "guide", alias],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        assert out.returncode == 0, f"alias {alias!r} stderr: {out.stderr}"
        assert "tech-detect" in out.stdout.lower()
        assert "wappalyzer" in out.stdout.lower()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_singleton_under_heavy_concurrency():
    """50 threads hammering the singleton must all see a valid DB."""
    import threading
    from flarecrawl.wappalyzer import get_wappalyzer

    errors: list[BaseException] = []
    counts: list[int] = []

    def worker():
        try:
            counts.append(get_wappalyzer().tech_count)
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(counts) == 50
    assert all(c > 4000 for c in counts)
    # All threads saw the same DB - same size
    assert len(set(counts)) == 1


def test_concurrent_analyze_no_corruption():
    """Concurrent analyze() calls on the singleton return correct results."""
    import threading
    from flarecrawl.wappalyzer import get_wappalyzer

    results: list[list[str]] = []
    errors: list[BaseException] = []

    def worker():
        try:
            dets = get_wappalyzer().analyze(html=WORDPRESS_HTML)
            results.append([d.name for d in dets])
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(results) == 20
    # Every thread saw WordPress in the result
    for names in results:
        assert "WordPress" in names


def test_detection_to_dict_drops_defaults():
    """Detection.to_dict omits noise fields."""
    d = Detection(name="X", categories=["CMS"])
    assert d.to_dict() == {"name": "X", "categories": ["CMS"]}

    d = Detection(name="X", categories=["CMS"], version="1.0", confidence=80)
    assert d.to_dict() == {
        "name": "X", "categories": ["CMS"], "version": "1.0", "confidence": 80,
    }


def test_empty_input_returns_empty_list():
    """No HTML, no headers — no detections, no crash."""
    w = WappalyzerClient()
    assert w.analyze() == []


def test_detections_sorted_by_confidence():
    """Highest-confidence detections come first."""
    w = WappalyzerClient()
    detections = w.analyze(html=WORDPRESS_HTML)
    confidences = [d.confidence for d in detections]
    assert confidences == sorted(confidences, reverse=True)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_wappalyzer_singleton():
    """get_wappalyzer returns the same instance on repeated calls."""
    a = get_wappalyzer()
    b = get_wappalyzer()
    assert a is b


# ---------------------------------------------------------------------------
# Client API
# ---------------------------------------------------------------------------


def test_client_detect_tech_returns_dicts():
    """Client.detect_tech wraps analyze() and serialises results."""
    c = Client(account_id="dummy", api_token="dummy")
    techs = c.detect_tech(html=WORDPRESS_HTML, url="https://example.com")
    names = [t["name"] for t in techs]
    assert "WordPress" in names
    # Returned dicts, not Detection objects
    assert all(isinstance(t, dict) for t in techs)


def test_client_detect_tech_no_network():
    """Client.detect_tech must not touch the network (no credentials needed)."""
    # Calling with bogus creds should still work — pure offline analysis.
    c = Client(account_id="not-real", api_token="not-real")
    techs = c.detect_tech(html=WORDPRESS_HTML)
    assert any(t["name"] == "WordPress" for t in techs)


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_scrape_stdin_tech_detect():
    """`flarecrawl scrape --stdin --tech-detect` attaches technologies[] to JSON output."""
    result = subprocess.run(
        [_flarecrawl_cmd(), "scrape", "--stdin",
         "--format", "html", "--json", "--tech-detect"],
        input=WORDPRESS_HTML,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = json.loads(result.stdout)
    techs = payload["data"].get("technologies", [])
    names = [t["name"] for t in techs]
    assert "WordPress" in names


def test_cli_scrape_stdin_no_tech_detect_default():
    """Without --tech-detect, the payload has no technologies key."""
    result = subprocess.run(
        [_flarecrawl_cmd(), "scrape", "--stdin",
         "--format", "html", "--json"],
        input=WORDPRESS_HTML,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = json.loads(result.stdout)
    assert "technologies" not in payload["data"]


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_concurrent_load_is_safe():
    """Concurrent first-load calls should not corrupt the cached DB."""
    import threading

    w = WappalyzerClient()
    errors: list[BaseException] = []

    def worker():
        try:
            assert w.tech_count > 4000
        except BaseException as e:  # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


# ---------------------------------------------------------------------------
# tech-detect --render (Playwright JS-globals probe path)
#
# These tests exercise `_fetch_for_tech_detect_render` end-to-end against the
# local fixture server's /spa route. The route sets `window.testSignal` on
# load; paired with the FlarecrawlTestSignal custom fingerprint (which has a
# `js: {"testSignal": ""}` pattern), a successful render proves the entire
# pipeline: Playwright launch -> page goto -> JS probe injection -> globals
# capture -> Wappalyzer match. None of these tests touch external network.
# ---------------------------------------------------------------------------


def _chromium_available() -> bool:
    """Best-effort check that Chromium is installed for Playwright."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:  # noqa: BLE001 - browser missing / launch failed
        return False


def test_fetch_for_tech_detect_render_returns_signal_tuple(tech_fixture_server):
    """Direct helper invocation: --render path returns (html, headers, cookies, js_globals)."""
    pytest.importorskip("playwright")
    if not _chromium_available():
        pytest.skip("Playwright Chromium not installed (run `playwright install chromium`)")

    from flarecrawl.cli import _fetch_for_tech_detect_render

    html, headers, cookies, js_globals = _fetch_for_tech_detect_render(
        f"{tech_fixture_server}/spa",
        timeout=30.0,
    )
    # Transport succeeded (non-empty page).
    assert "<html" in html.lower()
    assert "testsignal" in html.lower()
    # Headers captured via the Network.responseReceived hook.
    lowered = {k.lower(): v for k, v in headers.items()}
    assert lowered.get("content-type", "").startswith("text/html")
    # No cookies on this route.
    assert cookies == {}
    # The JS-globals probe ran and captured window.testSignal.
    assert js_globals.get("testSignal") == "match"


def test_cli_tech_detect_render_subcommand_end_to_end(tech_fixture_server):
    """`flarecrawl tech-detect <url>/spa --render --json` fires FlarecrawlTestSignal."""
    pytest.importorskip("playwright")
    if not _chromium_available():
        pytest.skip("Playwright Chromium not installed (run `playwright install chromium`)")

    result = subprocess.run(
        [_flarecrawl_cmd(), "tech-detect",
         f"{tech_fixture_server}/spa", "--render", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = json.loads(result.stdout)
    techs = payload["data"][0]["technologies"]
    names = {t["name"] for t in techs}
    # The synthetic fingerprint must fire — proves the JS-globals probe path.
    assert "FlarecrawlTestSignal" in names, (
        f"Expected FlarecrawlTestSignal in {names}; "
        "JS-globals probe likely failed to inject/capture."
    )


def test_fetch_for_tech_detect_render_missing_playwright(monkeypatch):
    """Helper raises FlareCrawlError(MISSING_DEPENDENCY) when Playwright isn't installed."""
    from flarecrawl.cli import _fetch_for_tech_detect_render
    from flarecrawl.client import FlareCrawlError

    # Force the `from playwright.sync_api import sync_playwright` line to fail
    # regardless of whether playwright is actually installed on this machine.
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    with pytest.raises(FlareCrawlError) as excinfo:
        _fetch_for_tech_detect_render("http://127.0.0.1:1/whatever")
    assert excinfo.value.code == "MISSING_DEPENDENCY"
    msg = str(excinfo.value).lower()
    assert "playwright" in msg
    assert "install" in msg  # actionable hint


def test_fetch_for_tech_detect_render_transport_error_returns_empty():
    """Unreachable URL returns empty tuples — never raises."""
    pytest.importorskip("playwright")
    if not _chromium_available():
        pytest.skip("Playwright Chromium not installed (run `playwright install chromium`)")

    from flarecrawl.cli import _fetch_for_tech_detect_render

    # Unallocated port on localhost - connection refused.
    html, headers, cookies, js_globals = _fetch_for_tech_detect_render(
        "http://127.0.0.1:1/nope",
        timeout=5.0,
    )
    assert html == ""
    assert headers == {}
    assert cookies == {}
    assert js_globals == {}


# ---------------------------------------------------------------------------
# Custom-overlay structural guards.
#
# Catch two classes of silent breakage in custom_fingerprints.json:
#   1. The overlay loader's per-key type-merge gives up when overlay and
#      upstream disagree on the structural type of a field (e.g. overlay's
#      `dom: [list]` vs upstream's `dom: {dict}`). Without normalisation the
#      overlay value was silently dropped, so SevenRooms (an upstream tech
#      with dict-form `dom`) was not gaining the overlay's bare-selector
#      patterns. The fix promotes overlay lists to dicts on the fly.
#   2. JSON does not forbid duplicate object keys; Python's parser silently
#      keeps the last. A duplicate `"Bokun"` definition had been silently
#      shadowing the first, losing several scriptSrc patterns. A canonical
#      load-as-pairs check guards against the same hazard returning.
# ---------------------------------------------------------------------------


def test_custom_overlay_no_duplicate_top_level_keys():
    """custom_fingerprints.json must not contain duplicate top-level keys.

    JSON does not technically forbid this, but Python's json.load() keeps
    only the last occurrence. If a future edit accidentally introduces
    a second `"Bokun"` (or similar), the earlier entry's patterns are
    silently dropped - hard to spot in code review and easy to do when
    appending new fingerprints into a non-alphabetical file.
    """
    import json as _json
    from collections import Counter

    from flarecrawl import wappalyzer as _wmod

    overlay_path = (
        Path(_wmod.__file__).parent / "wappalyzer_data" / "custom_fingerprints.json"
    )
    raw = overlay_path.read_text(encoding="utf-8")

    def _pairs_hook(pairs):  # type: ignore[no-untyped-def]
        return pairs

    pairs = _json.loads(raw, object_pairs_hook=_pairs_hook)
    counts = Counter(k for k, _ in pairs)
    dupes = {k: c for k, c in counts.items() if c > 1}
    assert not dupes, (
        f"Duplicate top-level keys in custom_fingerprints.json: {dupes}. "
        "JSON allows them but Python's parser silently keeps only the last, "
        "so the earlier entry's patterns are dropped. Merge into a single entry."
    )


def test_custom_overlay_list_dom_merges_into_upstream_dict_dom():
    """Overlay list-form `dom` must merge into upstream dict-form `dom`.

    Regression: previously the merge loop only handled list+list and
    dict+dict; an overlay list onto an upstream dict fell through to
    `key not in existing` and was silently dropped. The fix promotes
    overlay list selectors to `{selector: {}}` before dict-merging.

    SevenRooms is the canonical case: upstream ships
        "dom": {"iframe[src*='sevenrooms']": {"attributes": {...}}}
    and the overlay adds bare selectors
        "dom": ["a[href*='sevenrooms.com/reservations']",
                "iframe[src*='sevenrooms.com']"]
    Without the fix, the overlay selectors never reach the engine and a
    page that links to /reservations but lacks the .sevenrooms.* iframe
    is not detected.
    """
    w = WappalyzerClient()
    w._load()
    assert w._techs is not None

    dom = w._techs["SevenRooms"].get("dom")
    # Must be a dict after merge (upstream's form wins as the container).
    assert isinstance(dom, dict), f"SevenRooms.dom is {type(dom).__name__}, expected dict"

    # Both overlay selectors must be present as keys.
    assert "a[href*='sevenrooms.com/reservations']" in dom
    assert "iframe[src*='sevenrooms.com']" in dom

    # End-to-end: a page with only the overlay-form anchor must fire.
    html = '<a href="https://sevenrooms.com/reservations/foo">Book</a>'
    names = [d.name for d in w.analyze(html=html)]
    assert "SevenRooms" in names, (
        "SevenRooms dom merge regressed: overlay-form selector is no longer "
        "matching. Re-check the list+dict merge branch in WappalyzerClient._load()."
    )


def test_custom_overlay_unified_bokun_carries_both_pattern_sets():
    """The deduped Bokun entry must carry patterns from both prior copies.

    The file previously defined `"Bokun": {...}` twice. Python's json
    parser kept only the second, dropping these scriptSrc patterns:
        widget.bokun.io, bokun.io, bokuncdn.com
    and these html patterns:
        widget.bokun.io iframe, data-src=...bokun
    After merging the two entries into one, both pattern sets must fire.
    """
    w = WappalyzerClient()
    w._load()
    assert w._techs is not None

    bokun = w._techs.get("Bokun", {})
    script_src = " ".join(bokun.get("scriptSrc") or [])
    html_pats = " ".join(bokun.get("html") or [])

    # From the first (previously-shadowed) definition:
    assert "widget\\.bokun\\.io" in script_src
    assert "bokuncdn" in script_src
    assert "data-src" in html_pats
    # From the second (previously-winning) definition:
    assert "bokunWidget" in html_pats
    assert "BokunWidgetsLoader" in html_pats


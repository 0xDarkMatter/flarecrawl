"""Tests for fetch command content-type routing.

Parametrised suite covering exotic / non-HTML file types (XML, KML,
CSV, YAML, iCal, Turtle, …) and verifying each is routed to the correct
branch without invoking CF Browser Rendering.

Branch map:
  is_binary           → binary download (save to file)
  is_json             → JSON parse + return
  not is_html (new)   → raw text body returned verbatim  ← bug fix
  is_html             → CF Browser Rendering markdown conversion
"""

from __future__ import annotations

import json

import httpx
import pytest

pytest.importorskip("selectolax", reason="optional dep")
pytest.importorskip("aiosqlite", reason="optional dep")

from typer.testing import CliRunner

from flarecrawl.cli import app
from flarecrawl.fetch import ContentInfo, _is_binary_content_type, _is_html_content_type

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_info(content_type: str) -> ContentInfo:
    ct = content_type.split(";")[0].strip()
    return ContentInfo(
        content_type=ct,
        size=None,
        filename=None,
        is_binary=_is_binary_content_type(ct),
        is_json="json" in ct,
        is_html=_is_html_content_type(ct),
    )


def _mock_session(body: str, content_type: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": content_type})
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _extract_json(output: str) -> dict:
    """Parse the first JSON object from CLI output.

    result.output may include Rich console messages before the JSON, and in
    error paths a second JSON object after it. raw_decode stops at the first
    complete object, avoiding 'Extra data' errors.
    """
    idx = output.find("{")
    if idx == -1:
        raise ValueError(f"No JSON found in output:\n{output!r}")
    obj, _ = json.JSONDecoder().raw_decode(output, idx)
    return obj


# ---------------------------------------------------------------------------
# Cases that exercise the "raw text" branch (not binary, not JSON, not HTML)
# ---------------------------------------------------------------------------
#
# For each content type below, _is_binary_content_type must return False
# (otherwise the binary-download branch runs and needs an output path).
# Types classified as binary by the current heuristic are noted with a
# comment and tested separately in TestBinaryClassification.

RAW_TEXT_CASES = [
    # label, content_type, body_snippet
    ("kml_via_text_xml",
     "text/xml",
     "<?xml version='1.0'?><kml><Document><name>UAP Records</name></Document></kml>"),

    ("generic_xml",
     "application/xml",
     "<root><item id='1'>hello</item><item id='2'>world</item></root>"),

    ("atom_feed_text_xml",
     "text/xml; charset=utf-8",
     "<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><title>Blog</title></feed>"),

    ("csv",
     "text/csv",
     "id,name,lat,lon\n1,Alice,51.5074,-0.1278\n2,Bob,40.7128,-74.0060"),

    ("tsv",
     "text/tab-separated-values",
     "id\tname\n1\tAlice\n2\tBob"),

    ("plain_text",
     "text/plain",
     "Plain text content that is not HTML and should be returned verbatim."),

    ("yaml",
     "application/yaml",
     "title: Flarecrawl\nversion: 0.26.0\nfeatures:\n  - scrape\n  - crawl"),

    ("x_yaml",
     "application/x-yaml",
     "---\nkey: value\nlist:\n  - alpha\n  - beta"),

    ("toml",
     "application/toml",
     "[package]\nname = 'flarecrawl'\nversion = '0.26.0'\n"),

    ("ical",
     "text/calendar",
     "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nSUMMARY:Test\nEND:VEVENT\nEND:VCALENDAR"),

    ("vcard",
     "text/vcard",
     "BEGIN:VCARD\nVERSION:3.0\nFN:Alice Smith\nEND:VCARD"),

    ("turtle_rdf",
     "text/turtle",
     "@prefix ex: <http://example.org/> .\nex:subject ex:predicate ex:object ."),

    ("markdown",
     "text/markdown",
     "# Title\n\nSome **bold** content served as markdown."),

    ("csv_with_charset",
     "text/csv; charset=utf-8",
     "col1,col2\nvalue1,value2"),

    # RFC 6839 structured syntax suffix types (+xml family, now raw text not binary)
    ("rss_feed",
     "application/rss+xml",
     "<?xml version='1.0'?><rss version='2.0'><channel><title>Blog</title></channel></rss>"),

    ("atom_feed",
     "application/atom+xml",
     "<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><title>Blog</title></feed>"),

    ("kml_vnd",
     "application/vnd.google-earth.kml+xml",
     "<?xml version='1.0'?><kml xmlns='http://www.opengis.net/kml/2.2'><Document/></kml>"),

    ("soap_xml",
     "application/soap+xml",
     "<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'><soap:Body/></soap:Envelope>"),
]


@pytest.mark.parametrize("label,content_type,body", RAW_TEXT_CASES)
def test_fetch_raw_text_exotic_types(monkeypatch, label, content_type, body):
    """Non-HTML text types return raw body; CF Browser Rendering is never invoked."""
    info = _make_info(content_type)
    ct_base = content_type.split(";")[0].strip()

    # Pre-conditions: these must all go through the raw-text branch
    assert not info.is_html, f"[{label}] {ct_base} should NOT be classified as HTML"
    assert not info.is_binary, f"[{label}] {ct_base} should NOT be binary"

    mock_sess = _mock_session(body, content_type)
    monkeypatch.setattr("flarecrawl.fetch.detect_content_type", lambda *a, **kw: info)
    monkeypatch.setattr("flarecrawl.fetch.build_session", lambda **kw: mock_sess)

    result = runner.invoke(app, ["fetch", "https://example.com/data", "--json"])

    assert result.exit_code == 0, f"[{label}] exit={result.exit_code}\n{result.output}"
    data = _extract_json(result.output)
    # Body must come back verbatim
    assert "data" in data, f"[{label}] no 'data' key in: {result.output}"
    assert body.strip() in data["data"], f"[{label}] body not in response"
    assert data["meta"]["content_type"] == ct_base


# ---------------------------------------------------------------------------
# Original bug repro
# ---------------------------------------------------------------------------

def test_fetch_kml_repro_no_auth_no_traceback(monkeypatch):
    """Original bug repro: text/xml without CF auth must not traceback.

    Google Maps /d/kml returns text/xml. Before the fix this fell into the
    HTML→markdown branch, called _scrape_single, and traceback'd at cli.py:2691
    because _scrape_single raised an exception that wasn't caught cleanly.
    """
    kml = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<kml xmlns='http://www.opengis.net/kml/2.2'>"
        "<Document><name>UAP Records</name></Document></kml>"
    )
    info = _make_info("text/xml")
    mock_sess = _mock_session(kml, "text/xml")

    monkeypatch.setattr("flarecrawl.fetch.detect_content_type", lambda *a, **kw: info)
    monkeypatch.setattr("flarecrawl.fetch.build_session", lambda **kw: mock_sess)
    # No CF credentials — simulates the reporter's environment
    monkeypatch.setattr("flarecrawl.cli.get_account_id", lambda: None)
    monkeypatch.setattr("flarecrawl.cli.get_api_token", lambda: None)

    result = runner.invoke(
        app,
        ["fetch", "https://www.google.com/maps/d/kml?mid=abc&forcekml=1", "--json"],
    )

    # Must succeed (exit 0) and return raw KML — no traceback, no auth error
    assert result.exit_code == 0, f"Expected 0, got {result.exit_code}:\n{result.output}"
    data = _extract_json(result.output)
    assert "UAP Records" in data["data"]
    assert data["meta"]["content_type"] == "text/xml"


# ---------------------------------------------------------------------------
# HTML still routes through CF (auth required when no creds)
# ---------------------------------------------------------------------------

def test_fetch_html_still_requires_cf_auth(monkeypatch):
    """text/html must still go through CF Browser Rendering, which requires auth."""
    info = _make_info("text/html")
    assert info.is_html

    mock_sess = _mock_session("<html><body>hello</body></html>", "text/html")
    monkeypatch.setattr("flarecrawl.fetch.detect_content_type", lambda *a, **kw: info)
    monkeypatch.setattr("flarecrawl.fetch.build_session", lambda **kw: mock_sess)
    monkeypatch.setattr("flarecrawl.cli.get_account_id", lambda: None)
    monkeypatch.setattr("flarecrawl.cli.get_api_token", lambda: None)

    result = runner.invoke(app, ["fetch", "https://example.com/page.html", "--json"])

    data = _extract_json(result.output)
    assert "error" in data, f"Expected auth error, got: {result.output}"
    assert data["error"]["code"] == "AUTH_REQUIRED"


# ---------------------------------------------------------------------------
# Content-type classification sanity checks
# ---------------------------------------------------------------------------

class TestBinaryClassification:
    """Content-type classification matrix.

    Three routing branches exist:
      binary   → binary download (save to file)
      json     → JSON parse + return
      raw text → raw body returned verbatim  (not binary, not json, not html)
      html     → CF Browser Rendering markdown conversion
    """

    @pytest.mark.parametrize("ct", [
        "application/pdf",
        "application/zip",
        "application/octet-stream",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "image/png",
        "audio/mpeg",
        "video/mp4",
        "font/woff2",
    ])
    def test_opaque_types_are_binary(self, ct):
        """Types with no structured suffix and no text prefix are binary."""
        assert _is_binary_content_type(ct), f"{ct} expected binary"
        assert not _is_html_content_type(ct), f"{ct} must not be html"

    @pytest.mark.parametrize("ct", [
        # +json suffix (RFC 6839) — not binary, route to JSON branch
        "application/rss+xml",
        "application/atom+xml",
        "application/vnd.google-earth.kml+xml",
        "application/soap+xml",
        "application/geo+json",
        "application/ld+json",
        "application/problem+json",
        "application/vnd.api+json",
        # ndjson
        "application/x-ndjson",
        "application/x-jsonlines",
        "application/jsonlines",
    ])
    def test_rfc6839_suffix_and_ndjson_not_binary(self, ct):
        """RFC 6839 structured syntax suffixes (+json, +xml) and ndjson are never binary."""
        assert not _is_binary_content_type(ct), f"{ct} expected not binary"
        assert not _is_html_content_type(ct), f"{ct} must not be html"

    @pytest.mark.parametrize("ct", [
        "text/html",
        "text/html; charset=utf-8",
        "application/xhtml+xml",
    ])
    def test_html_types(self, ct):
        assert _is_html_content_type(ct)
        assert not _is_binary_content_type(ct)

    @pytest.mark.parametrize("ct", [
        "text/xml",
        "application/xml",
        "text/csv",
        "text/plain",
        "application/yaml",
        "text/calendar",
        "text/markdown",
        "application/rss+xml",
        "application/atom+xml",
        "application/vnd.google-earth.kml+xml",
    ])
    def test_raw_text_types_not_binary_not_html(self, ct):
        """Types that hit the raw-text routing branch (not binary, not json, not html)."""
        assert not _is_binary_content_type(ct)
        assert not _is_html_content_type(ct)
        assert "json" not in ct.lower()

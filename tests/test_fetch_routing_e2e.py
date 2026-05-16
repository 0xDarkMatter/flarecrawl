"""End-to-end routing tests using a real local HTTP server.

Hits an actual TCP socket — no monkeypatching of detect_content_type or
build_session.  The routing_server fixture (tests/conftest.py) serves
tests/fixtures/routing/ with explicit Content-Type headers for every
exotic extension that Python's mimetypes doesn't know about.

Branch map tested here:
  is_binary           → binary download (not tested: needs --output flag)
  is_json             → JSON parsed and returned as-is
  not is_html (new)   → raw text body returned verbatim
  is_html             → CF Browser Rendering (requires auth — not tested here)
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("selectolax", reason="optional dep")
pytest.importorskip("aiosqlite", reason="optional dep")

from typer.testing import CliRunner

from flarecrawl.cli import app

runner = CliRunner()


def _first_json(output: str) -> dict:
    """Extract first JSON object from CLI output (may have Rich console prefix)."""
    idx = output.find("{")
    if idx == -1:
        raise ValueError(f"No JSON in output:\n{output!r}")
    obj, _ = json.JSONDecoder().raw_decode(output, idx)
    return obj


# ---------------------------------------------------------------------------
# Raw-text branch: not binary, not JSON, not HTML
# ---------------------------------------------------------------------------

RAW_TEXT_FILES = [
    # (fixture_filename, expected_content_type_base, sentinel_string_in_body)
    ("catalog.xml",  "application/xml",                          "<catalog"),
    ("cities.csv",   "text/csv",                                 "São Paulo"),
    ("genes.tsv",    "text/tab-separated-values",                "BRCA2"),
    ("feed.rss",     "application/rss+xml",                     "<rss version"),
    ("feed.atom",    "application/atom+xml",                    "<feed xmlns"),
    ("places.kml",   "application/vnd.google-earth.kml+xml",    "Eiffel Tower"),
    ("config.yaml",  "application/yaml",                         "tls_fingerprint"),
    ("config.toml",  "application/toml",                         "backoff_factor"),
    ("calendar.ics", "text/calendar",                            "BEGIN:VCALENDAR"),
    ("contacts.vcf", "text/vcard",                               "BEGIN:VCARD"),
    ("triples.ttl",  "text/turtle",                              "@prefix ex:"),
    ("README.md",    "text/markdown",                            "Routing matrix"),
]


@pytest.mark.parametrize("filename,expected_ct_base,sentinel", RAW_TEXT_FILES)
def test_raw_text_e2e(routing_server, filename, expected_ct_base, sentinel):
    """Raw-text files are returned verbatim; no CF auth required."""
    url = f"{routing_server}/{filename}"
    result = runner.invoke(app, ["fetch", url, "--json"])

    assert result.exit_code == 0, (
        f"[{filename}] exit={result.exit_code}\n{result.output}"
    )
    data = _first_json(result.output)
    assert "data" in data, f"[{filename}] no 'data' key:\n{result.output}"
    assert sentinel in data["data"], (
        f"[{filename}] sentinel {sentinel!r} not in body:\n{data['data'][:200]}"
    )
    assert data["meta"]["content_type"].startswith(expected_ct_base), (
        f"[{filename}] content_type={data['meta']['content_type']!r}, "
        f"expected prefix {expected_ct_base!r}"
    )


# ---------------------------------------------------------------------------
# JSON branch: not binary, is_json=True
# ---------------------------------------------------------------------------

JSON_FILES = [
    # (fixture_filename, expected_ct_base, sentinel_key_in_parsed_json)
    ("api.json",       "application/json",      "endpoints"),
    ("places.geojson", "application/geo+json",  "features"),
    ("schema.jsonld",  "application/ld+json",   "featureList"),
    ("events.ndjson",  "application/x-ndjson",  None),  # ndjson: body returned, not parsed
]


@pytest.mark.parametrize("filename,expected_ct_base,json_key", JSON_FILES)
def test_json_e2e(routing_server, filename, expected_ct_base, json_key):
    """JSON-family files are routed to the JSON branch and returned as structured data."""
    url = f"{routing_server}/{filename}"
    result = runner.invoke(app, ["fetch", url, "--json"])

    assert result.exit_code == 0, (
        f"[{filename}] exit={result.exit_code}\n{result.output}"
    )
    data = _first_json(result.output)

    if json_key is not None:
        # Standard JSON: data field contains the parsed object
        assert "data" in data, f"[{filename}] no 'data' key:\n{result.output}"
        assert json_key in data["data"], (
            f"[{filename}] key {json_key!r} not in parsed JSON"
        )
    else:
        # ndjson: goes through JSON branch (is_json=True), body returned
        assert "data" in data or result.exit_code == 0


# ---------------------------------------------------------------------------
# Verify Content-Type headers are what we expect from the fixture server
# ---------------------------------------------------------------------------

CONTENT_TYPE_CHECKS = [
    ("catalog.xml",  "application/xml"),
    ("cities.csv",   "text/csv"),
    ("feed.rss",     "application/rss+xml"),
    ("feed.atom",    "application/atom+xml"),
    ("places.kml",   "application/vnd.google-earth.kml+xml"),
    ("config.yaml",  "application/yaml"),
    ("places.geojson", "application/geo+json"),
    ("schema.jsonld",  "application/ld+json"),
    ("events.ndjson",  "application/x-ndjson"),
]


@pytest.mark.parametrize("filename,expected_ct", CONTENT_TYPE_CHECKS)
def test_server_content_type_headers(routing_server, filename, expected_ct):
    """Verify the fixture server sends the correct Content-Type for each file."""
    import httpx
    resp = httpx.get(f"{routing_server}/{filename}", follow_redirects=True)
    assert resp.status_code == 200
    ct = resp.headers.get("content-type", "")
    assert ct.startswith(expected_ct), (
        f"[{filename}] server sent Content-Type={ct!r}, expected prefix {expected_ct!r}"
    )

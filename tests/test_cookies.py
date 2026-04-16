"""Tests for cookie loading, format conversion, and domain filtering."""

import json
from pathlib import Path

import httpx
import pytest

from flarecrawl.cookies import (
    _domain_matches,
    _load_netscape_cookies,
    cookies_to_header,
    cookies_to_httpx,
    load_cookies,
)


class TestLoadCookies:
    """Test load_cookies() auto-detect format."""

    def test_load_puppeteer_json_array(self, tmp_path):
        data = [
            {"name": "session", "value": "abc123", "domain": ".example.com", "path": "/"},
            {"name": "user", "value": "42", "domain": ".example.com", "path": "/"},
        ]
        f = tmp_path / "cookies.json"
        f.write_text(json.dumps(data))
        cookies = load_cookies(f)
        assert len(cookies) == 2
        assert cookies[0]["name"] == "session"
        assert cookies[0]["value"] == "abc123"

    def test_load_chrome_devtools_nested(self, tmp_path):
        data = {"cookies": [{"name": "tok", "value": "xyz", "domain": "example.com", "path": "/"}]}
        f = tmp_path / "cookies.json"
        f.write_text(json.dumps(data))
        cookies = load_cookies(f)
        assert len(cookies) == 1
        assert cookies[0]["name"] == "tok"

    def test_load_netscape_format(self, tmp_path):
        content = (
            "# Netscape HTTP Cookie File\n"
            ".example.com\tTRUE\t/\tFALSE\t0\tsession_id\tabc\n"
            ".example.com\tTRUE\t/\tTRUE\t1735689600\tsecure_token\txyz\n"
        )
        f = tmp_path / "cookies.txt"
        f.write_text(content)
        cookies = load_cookies(f)
        assert len(cookies) == 2
        assert cookies[0]["name"] == "session_id"
        assert cookies[0]["secure"] is False
        assert cookies[1]["name"] == "secure_token"
        assert cookies[1]["secure"] is True
        assert cookies[1]["expires"] == 1735689600

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("")
        cookies = load_cookies(f)
        assert cookies == []

    def test_json_missing_name_or_value_skipped(self, tmp_path):
        data = [
            {"name": "good", "value": "v1"},
            {"name": "bad"},          # missing value
            {"value": "v2"},          # missing name
        ]
        f = tmp_path / "cookies.json"
        f.write_text(json.dumps(data))
        cookies = load_cookies(f)
        assert len(cookies) == 1
        assert cookies[0]["name"] == "good"

    def test_json_preserves_optional_fields(self, tmp_path):
        data = [{"name": "a", "value": "b", "domain": ".x.com", "path": "/p",
                 "httpOnly": True, "secure": True, "sameSite": "Strict"}]
        f = tmp_path / "cookies.json"
        f.write_text(json.dumps(data))
        cookies = load_cookies(f)
        assert cookies[0]["httpOnly"] is True
        assert cookies[0]["secure"] is True
        assert cookies[0]["sameSite"] == "Strict"

    def test_netscape_skips_short_lines(self, tmp_path):
        content = ".example.com\tTRUE\t/\n"  # only 3 fields
        f = tmp_path / "cookies.txt"
        f.write_text(content)
        cookies = load_cookies(f)
        assert cookies == []


class TestCookiesToHttpx:
    """Test cookies_to_httpx conversion."""

    def test_basic_conversion(self):
        cookies = [
            {"name": "a", "value": "1", "domain": ".example.com", "path": "/"},
            {"name": "b", "value": "2", "domain": ".example.com", "path": "/"},
        ]
        jar = cookies_to_httpx(cookies)
        assert isinstance(jar, httpx.Cookies)

    def test_empty_list(self):
        jar = cookies_to_httpx([])
        assert isinstance(jar, httpx.Cookies)


class TestCookiesToHeader:
    """Test cookies_to_header domain filtering."""

    def _cookies(self):
        return [
            {"name": "a", "value": "1", "domain": ".example.com"},
            {"name": "b", "value": "2", "domain": ".other.com"},
            {"name": "c", "value": "3", "domain": ""},
        ]

    def test_matching_domain(self):
        header = cookies_to_header(self._cookies(), "www.example.com")
        assert "a=1" in header
        assert "b=2" not in header
        assert "c=3" in header  # empty domain matches all

    def test_no_match(self):
        header = cookies_to_header(self._cookies(), "totally.different.com")
        assert "a=1" not in header
        assert "b=2" not in header
        assert "c=3" in header  # empty domain matches all

    def test_exact_subdomain_match(self):
        cookies = [{"name": "x", "value": "y", "domain": "api.example.com"}]
        header = cookies_to_header(cookies, "api.example.com")
        assert "x=y" in header

    def test_empty_cookies_returns_empty_string(self):
        assert cookies_to_header([], "example.com") == ""


class TestDomainMatches:
    """Test _domain_matches helper."""

    def test_exact_match(self):
        assert _domain_matches("example.com", "example.com")

    def test_dot_prefix_matches_subdomain(self):
        assert _domain_matches(".example.com", "www.example.com")

    def test_no_match(self):
        assert not _domain_matches(".other.com", "example.com")

    def test_empty_cookie_domain_always_matches(self):
        assert _domain_matches("", "anything.com")

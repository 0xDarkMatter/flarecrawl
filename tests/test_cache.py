"""Tests for the cache module."""

import json
import time

import pytest

from flarecrawl.cache import _cache_key, clear, get, put


class TestCacheKey:
    """Test cache key generation."""

    def test_deterministic(self):
        key1 = _cache_key("markdown", {"url": "https://example.com"})
        key2 = _cache_key("markdown", {"url": "https://example.com"})
        assert key1 == key2

    def test_different_endpoints(self):
        key1 = _cache_key("markdown", {"url": "https://example.com"})
        key2 = _cache_key("content", {"url": "https://example.com"})
        assert key1 != key2

    def test_different_urls(self):
        key1 = _cache_key("markdown", {"url": "https://a.com"})
        key2 = _cache_key("markdown", {"url": "https://b.com"})
        assert key1 != key2

    def test_key_length(self):
        key = _cache_key("markdown", {"url": "https://example.com"})
        assert len(key) == 16

    def test_body_order_independent(self):
        key1 = _cache_key("markdown", {"url": "https://example.com", "timeout": 5000})
        key2 = _cache_key("markdown", {"timeout": 5000, "url": "https://example.com"})
        assert key1 == key2


class TestCachePutGet:
    """Test cache put/get cycle."""

    def test_put_and_get(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.cache.get_config_dir", lambda: tmp_path)
        body = {"url": "https://example.com"}
        response = {"result": "# Hello"}

        put("markdown", body, response)
        cached = get("markdown", body, ttl=3600)

        assert cached == response

    def test_miss_on_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.cache.get_config_dir", lambda: tmp_path)
        result = get("markdown", {"url": "https://nonexistent.com"}, ttl=3600)
        assert result is None

    def test_ttl_expiry(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.cache.get_config_dir", lambda: tmp_path)
        body = {"url": "https://example.com"}
        put("markdown", body, {"result": "old"})

        # Manually expire the cache entry
        from flarecrawl.cache import _cache_dir, _cache_key
        cache_file = _cache_dir() / f"{_cache_key('markdown', body)}.json"
        data = json.loads(cache_file.read_text())
        data["_cached_at"] = time.time() - 7200  # 2 hours ago
        cache_file.write_text(json.dumps(data))

        result = get("markdown", body, ttl=3600)
        assert result is None

    def test_different_body_different_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.cache.get_config_dir", lambda: tmp_path)
        put("markdown", {"url": "https://a.com"}, {"result": "A"})
        put("markdown", {"url": "https://b.com"}, {"result": "B"})

        assert get("markdown", {"url": "https://a.com"}, ttl=3600) == {"result": "A"}
        assert get("markdown", {"url": "https://b.com"}, ttl=3600) == {"result": "B"}


class TestCacheClear:
    """Test cache clearing."""

    def test_clear_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.cache.get_config_dir", lambda: tmp_path)
        count = clear()
        assert count == 0

    def test_clear_with_entries(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.cache.get_config_dir", lambda: tmp_path)
        put("markdown", {"url": "https://a.com"}, {"result": "A"})
        put("markdown", {"url": "https://b.com"}, {"result": "B"})

        count = clear()
        assert count == 2

        # Verify cleared
        assert get("markdown", {"url": "https://a.com"}, ttl=3600) is None


class TestCacheablePredicate:
    """v0.23.0 P1.3: skip caching empty/error/stub responses."""

    def test_empty_string_not_cacheable(self):
        from flarecrawl.cache import cacheable_response
        assert cacheable_response("") is False

    def test_non_empty_string_cacheable(self):
        from flarecrawl.cache import cacheable_response
        assert cacheable_response("# Hello\n\nFull markdown content here.") is True

    def test_empty_list_not_cacheable(self):
        from flarecrawl.cache import cacheable_response
        assert cacheable_response([]) is False

    def test_non_empty_list_cacheable(self):
        from flarecrawl.cache import cacheable_response
        assert cacheable_response(["https://a", "https://b"]) is True

    def test_dict_with_403_not_cacheable(self):
        from flarecrawl.cache import cacheable_response
        assert cacheable_response({"status": 403, "content": "Forbidden"}) is False

    def test_dict_with_500_not_cacheable(self):
        from flarecrawl.cache import cacheable_response
        assert cacheable_response({"status": 500, "content": "x" * 5000}) is False

    def test_dict_with_200_and_real_content_cacheable(self):
        from flarecrawl.cache import cacheable_response
        big_content = "x" * 5000
        assert cacheable_response({"status": 200, "content": big_content, "format": "markdown"}) is True

    def test_dict_with_empty_content_not_cacheable(self):
        from flarecrawl.cache import cacheable_response
        assert cacheable_response({"content": "", "format": "html"}) is False

    def test_dict_with_stub_html_not_cacheable(self):
        from flarecrawl.cache import cacheable_response
        # 293 bytes — the war.gov stub size
        stub = "<html><body>Bot detection placeholder</body></html>" * 4
        assert len(stub) < 1024
        assert cacheable_response({"content": stub, "format": "html"}) is False

    def test_dict_with_stub_markdown_not_cacheable(self):
        from flarecrawl.cache import cacheable_response
        assert cacheable_response({"content": "Tiny page", "format": "markdown"}) is False

    def test_short_content_in_unknown_format_passes(self):
        # Non-html/markdown formats (e.g. links) have their own checks
        from flarecrawl.cache import cacheable_response
        assert cacheable_response({"content": "x", "format": "links"}) is True

    def test_allow_empty_override(self):
        from flarecrawl.cache import cacheable_response
        assert cacheable_response("", allow_empty=True) is True
        assert cacheable_response({"status": 403}, allow_empty=True) is True

    def test_nested_data_content(self):
        # Some endpoints wrap as {"data": {"content": ...}}
        from flarecrawl.cache import cacheable_response
        assert cacheable_response({"data": {"content": ""}, "meta": {"format": "html"}}) is False
        assert cacheable_response({"data": {"content": "x" * 5000}, "meta": {"format": "html"}}) is True


class TestPutGating:
    """v0.23.0 P1.3: cache.put() now refuses empty/error responses."""

    def test_put_skips_empty_response(self, tmp_path, monkeypatch):
        from flarecrawl.cache import get, put
        monkeypatch.setattr("flarecrawl.cache.get_config_dir", lambda: tmp_path)
        ok = put("markdown", {"url": "https://a.com"}, "")
        assert ok is False
        assert get("markdown", {"url": "https://a.com"}, ttl=3600) is None

    def test_put_skips_403_response(self, tmp_path, monkeypatch):
        from flarecrawl.cache import get, put
        monkeypatch.setattr("flarecrawl.cache.get_config_dir", lambda: tmp_path)
        ok = put("markdown", {"url": "https://a.com"}, {"status": 403, "content": "Forbidden"})
        assert ok is False
        assert get("markdown", {"url": "https://a.com"}, ttl=3600) is None

    def test_put_persists_real_response(self, tmp_path, monkeypatch):
        from flarecrawl.cache import get, put
        monkeypatch.setattr("flarecrawl.cache.get_config_dir", lambda: tmp_path)
        good = {"status": 200, "content": "x" * 5000, "format": "markdown"}
        ok = put("markdown", {"url": "https://a.com"}, good)
        assert ok is True
        assert get("markdown", {"url": "https://a.com"}, ttl=3600) == good

    def test_allow_empty_keeps_legacy_behaviour(self, tmp_path, monkeypatch):
        from flarecrawl.cache import get, put
        monkeypatch.setattr("flarecrawl.cache.get_config_dir", lambda: tmp_path)
        ok = put("markdown", {"url": "https://a.com"}, "", allow_empty=True)
        assert ok is True
        assert get("markdown", {"url": "https://a.com"}, ttl=3600) == ""


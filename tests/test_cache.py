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

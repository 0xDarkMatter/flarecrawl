"""Tests for v0.25.0 P3.3: DataSourceProbe."""

from __future__ import annotations


def _make_response_event(url: str, mime: str, size: int = 5000, status: int = 200) -> dict:
    return {
        "requestId": "req1",
        "response": {
            "url": url,
            "mimeType": mime,
            "status": status,
            "headers": {"content-length": str(size)},
        },
    }


class TestPatternMatching:
    def test_csv_detected(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe(page_origin="https://example.com")
        p._on_response_received(_make_response_event(
            "https://example.com/data/items.csv", "text/csv; charset=utf-8"
        ))
        assert len(p.detected) == 1
        assert p.detected[0]["url"] == "https://example.com/data/items.csv"
        assert p.detected[0]["content_type"] == "text/csv; charset=utf-8"

    def test_json_detected(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe(page_origin="https://example.com")
        p._on_response_received(_make_response_event(
            "https://example.com/api/manifest", "application/json"
        ))
        assert len(p.detected) == 1

    def test_xlsx_detected(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe(page_origin="https://example.com")
        p._on_response_received(_make_response_event(
            "https://example.com/export.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ))
        assert len(p.detected) == 1

    def test_html_not_detected(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe(page_origin="https://example.com")
        p._on_response_received(_make_response_event(
            "https://example.com/page", "text/html"
        ))
        assert p.detected == []

    def test_image_not_detected(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe(page_origin="https://example.com")
        p._on_response_received(_make_response_event(
            "https://example.com/icon.png", "image/png"
        ))
        assert p.detected == []


class TestSizeThreshold:
    def test_below_threshold_skipped(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe(min_size=1024, page_origin="https://example.com")
        p._on_response_received(_make_response_event(
            "https://example.com/tiny.csv", "text/csv", size=500
        ))
        assert p.detected == []

    def test_above_threshold_kept(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe(min_size=1024, page_origin="https://example.com")
        p._on_response_received(_make_response_event(
            "https://example.com/big.csv", "text/csv", size=10000
        ))
        assert len(p.detected) == 1

    def test_unknown_size_kept(self):
        """When Content-Length is missing, we keep the entry (defer to caller)."""
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe(min_size=1024, page_origin="https://example.com")
        evt = _make_response_event("https://example.com/x.csv", "text/csv")
        evt["response"]["headers"] = {}  # strip content-length
        evt["response"]["encodedDataLength"] = 0
        p._on_response_received(evt)
        # size=0 is treated as unknown and passes through
        assert len(p.detected) == 1


class TestSameOrigin:
    def test_cross_origin_blocked_by_default(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe(page_origin="https://example.com")
        p._on_response_received(_make_response_event(
            "https://other.com/data.csv", "text/csv"
        ))
        assert p.detected == []

    def test_cross_origin_allowed_with_flag(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe(page_origin="https://example.com", same_origin_only=False)
        p._on_response_received(_make_response_event(
            "https://other.com/data.csv", "text/csv"
        ))
        assert len(p.detected) == 1

    def test_no_page_origin_skips_filter(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe()  # no page_origin = no same-origin enforcement
        p._on_response_received(_make_response_event(
            "https://anywhere.com/x.csv", "text/csv"
        ))
        assert len(p.detected) == 1


class TestDedupe:
    def test_same_url_recorded_once(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe(page_origin="https://example.com")
        p._on_response_received(_make_response_event(
            "https://example.com/x.csv", "text/csv"
        ))
        p._on_response_received(_make_response_event(
            "https://example.com/x.csv", "text/csv"
        ))
        assert len(p.detected) == 1


class TestSetPageOrigin:
    def test_can_set_after_construction(self):
        from flarecrawl.cdp import DataSourceProbe
        p = DataSourceProbe()
        p.set_page_origin("https://example.com/some/page")
        # Cross-origin should now be blocked
        p._on_response_received(_make_response_event(
            "https://other.com/data.csv", "text/csv"
        ))
        assert p.detected == []
        p._on_response_received(_make_response_event(
            "https://example.com/data.csv", "text/csv"
        ))
        assert len(p.detected) == 1

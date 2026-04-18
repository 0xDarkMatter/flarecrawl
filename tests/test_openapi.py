"""Tests for OpenAPI/Swagger spec discovery and validation."""

import pytest
pytest.importorskip("selectolax", reason="optional dep")
pytest.importorskip("aiosqlite", reason="optional dep")
import json

import pytest

from flarecrawl.openapi import (
    COMMON_SPEC_PATHS,
    SpecDiscovery,
    SpecValidation,
    discover_specs,
    validate_spec,
)


class TestDiscoverSpecs:
    """Test discover_specs() HTML parsing."""

    def test_finds_spec_link_in_anchor(self):
        html = '<html><body><a href="/api/swagger.json">API Docs</a></body></html>'
        specs = discover_specs(html, "https://example.com")
        assert any(s.url == "https://example.com/api/swagger.json" for s in specs)

    def test_finds_openapi_json_link(self):
        html = '<html><body><a href="/openapi.json">OpenAPI</a></body></html>'
        specs = discover_specs(html, "https://example.com")
        urls = [s.url for s in specs]
        assert "https://example.com/openapi.json" in urls

    def test_finds_swaggerui_url_in_script(self):
        html = """<html><body>
        <script>SwaggerUIBundle({ url: "/swagger/v1/swagger.json" })</script>
        </body></html>"""
        specs = discover_specs(html, "https://example.com")
        urls = [s.url for s in specs]
        assert "https://example.com/swagger/v1/swagger.json" in urls

    def test_swaggerui_source_has_high_confidence(self):
        html = '<script>const ui = SwaggerUIBundle({ url: "/api.json" })</script>'
        specs = discover_specs(html, "https://example.com")
        swagger_specs = [s for s in specs if s.source == "swagger-ui"]
        assert all(s.confidence >= 0.9 for s in swagger_specs)

    def test_finds_text_match_link(self):
        html = '<html><body><a href="/docs/api-spec.json">Download OpenAPI Spec</a></body></html>'
        specs = discover_specs(html, "https://example.com")
        text_specs = [s for s in specs if s.source == "text-match"]
        assert len(text_specs) >= 1

    def test_format_detection_json(self):
        html = '<a href="/openapi.json">API</a>'
        specs = discover_specs(html, "https://example.com")
        json_specs = [s for s in specs if s.url.endswith(".json")]
        assert all(s.format == "json" for s in json_specs)

    def test_format_detection_yaml(self):
        html = '<a href="/openapi.yaml">API</a>'
        specs = discover_specs(html, "https://example.com")
        yaml_specs = [s for s in specs if s.url.endswith(".yaml")]
        assert all(s.format == "yaml" for s in yaml_specs)

    def test_empty_html_returns_empty(self):
        specs = discover_specs("<html><body></body></html>", "https://example.com")
        assert specs == []

    def test_deduplicates_urls(self):
        html = """<html><body>
        <a href="/openapi.json">Link 1</a>
        <a href="/openapi.json">Link 2</a>
        </body></html>"""
        specs = discover_specs(html, "https://example.com")
        urls = [s.url for s in specs]
        assert len(urls) == len(set(urls))

    def test_resolves_relative_urls(self):
        html = '<a href="swagger.json">API</a>'
        specs = discover_specs(html, "https://example.com/docs/")
        assert any("example.com" in s.url for s in specs)


class TestValidateSpec:
    """Test validate_spec() for openapi/swagger detection."""

    def test_valid_openapi_3(self):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "My API", "version": "1.0"},
            "paths": {
                "/pets": {"get": {}, "post": {}},
                "/pets/{id}": {"delete": {}},
            },
        }
        result = validate_spec(spec)
        assert result.valid is True
        assert result.version == "3.0.0"
        assert result.title == "My API"
        assert result.endpoint_count == 3

    def test_valid_swagger_2(self):
        spec = {
            "swagger": "2.0",
            "info": {"title": "Test", "version": "1"},
            "paths": {"/users": {"get": {}}},
        }
        result = validate_spec(spec)
        assert result.valid is True
        assert result.version == "2.0"

    def test_invalid_spec_no_openapi_key(self):
        spec = {"title": "Not an OpenAPI spec"}
        result = validate_spec(spec)
        assert result.valid is False

    def test_json_string_input(self):
        data = {"openapi": "3.0.0", "info": {"title": "T", "version": "1"}, "paths": {}}
        result = validate_spec(json.dumps(data))
        assert result.valid is True

    def test_invalid_json_returns_invalid(self):
        result = validate_spec("not valid json at all{{{")
        assert result.valid is False

    def test_empty_dict_returns_invalid(self):
        result = validate_spec({})
        assert result.valid is False

    def test_no_paths_has_none_endpoint_count(self):
        spec = {"openapi": "3.0.0", "info": {"title": "T", "version": "1"}}
        result = validate_spec(spec)
        assert result.endpoint_count is None

    def test_paths_counts_only_http_methods(self):
        spec = {
            "openapi": "3.0.0",
            "paths": {
                "/a": {"get": {}, "post": {}, "summary": "should not count", "parameters": []},
            },
        }
        result = validate_spec(spec)
        assert result.endpoint_count == 2


class TestCommonSpecPaths:
    """Test the COMMON_SPEC_PATHS list."""

    def test_includes_standard_paths(self):
        assert "/swagger.json" in COMMON_SPEC_PATHS
        assert "/openapi.json" in COMMON_SPEC_PATHS
        assert "/openapi.yaml" in COMMON_SPEC_PATHS

    def test_includes_versioned_paths(self):
        assert "/v2/api-docs" in COMMON_SPEC_PATHS
        assert "/v3/api-docs" in COMMON_SPEC_PATHS

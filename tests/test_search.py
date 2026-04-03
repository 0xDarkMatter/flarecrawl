"""Tests for web search module."""

import json
from unittest.mock import MagicMock, patch

from flarecrawl.search import SearchResult, jina_search


class TestJinaSearch:
    """Test Jina Search API client."""

    @patch("flarecrawl.search.httpx.Client")
    def test_returns_results(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"url": "https://example.com/a", "title": "Result A", "description": "Snippet A"},
                {"url": "https://example.com/b", "title": "Result B", "description": "Snippet B"},
            ]
        }
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        results = jina_search("test query")
        assert len(results) == 2
        assert results[0].url == "https://example.com/a"
        assert results[0].title == "Result A"
        assert results[0].snippet == "Snippet A"

    @patch("flarecrawl.search.httpx.Client")
    def test_respects_limit(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"url": f"https://example.com/{i}", "title": f"R{i}", "description": f"S{i}"}
                for i in range(20)
            ]
        }
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        results = jina_search("test", limit=5)
        assert len(results) == 5

    @patch("flarecrawl.search.httpx.Client")
    def test_empty_results(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        results = jina_search("obscure query")
        assert results == []

    @patch("flarecrawl.search.httpx.Client")
    def test_truncates_long_snippets(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"url": "https://x.com", "title": "T", "description": "A" * 1000}]
        }
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        results = jina_search("test")
        assert len(results[0].snippet) == 500


class TestSearchCliCommand:
    """Test search CLI command."""

    def test_search_in_help(self):
        from typer.testing import CliRunner
        from flarecrawl.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["search", "--help"])
        assert result.exit_code == 0
        assert "--limit" in result.output
        assert "--scrape" in result.output
        assert "--json" in result.output
        assert "--proxy" in result.output
        assert "--paywall" in result.output
        assert "--stealth" in result.output

    @patch("flarecrawl.search.jina_search")
    def test_search_json_output(self, mock_search):
        mock_search.return_value = [
            SearchResult(url="https://example.com", title="Test", snippet="A snippet"),
        ]
        from typer.testing import CliRunner
        from flarecrawl.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["search", "test query", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["meta"]["query"] == "test query"
        assert len(data["data"]) == 1
        assert data["data"][0]["url"] == "https://example.com"

"""Tests for per-site YAML rulesets."""

import pytest
from pathlib import Path

from flarecrawl.rules import (
    _parse_yaml,
    _rules_to_dict,
    get_site_headers,
    load_rules,
    clear_cache,
)


class TestParseYaml:
    """Test YAML parsing."""

    def test_single_domain(self, tmp_path):
        f = tmp_path / "rules.yaml"
        f.write_text('- domain: example.com\n  headers:\n    Referer: "https://google.com/"')
        result = _parse_yaml(f)
        assert len(result) == 1
        assert result[0]["domain"] == "example.com"

    def test_multiple_domains(self, tmp_path):
        f = tmp_path / "rules.yaml"
        f.write_text('- domains:\n    - a.com\n    - b.com\n  headers:\n    Referer: "https://google.com/"')
        result = _parse_yaml(f)
        assert len(result) == 1
        assert result[0]["domains"] == ["a.com", "b.com"]

    def test_missing_file(self, tmp_path):
        result = _parse_yaml(tmp_path / "nope.yaml")
        assert result == []

    def test_empty_file(self, tmp_path):
        f = tmp_path / "rules.yaml"
        f.write_text("")
        result = _parse_yaml(f)
        assert result == []


class TestRulesToDict:
    """Test rule entry conversion."""

    def test_single_domain_entry(self):
        entries = [{"domain": "example.com", "headers": {"Referer": "https://google.com/"}}]
        result = _rules_to_dict(entries)
        assert result == {"example.com": {"Referer": "https://google.com/"}}

    def test_multi_domain_entry(self):
        entries = [{"domains": ["a.com", "b.com"], "headers": {"Referer": "https://google.com/"}}]
        result = _rules_to_dict(entries)
        assert "a.com" in result
        assert "b.com" in result
        assert result["a.com"] == result["b.com"]

    def test_mixed_entries(self):
        entries = [
            {"domain": "single.com", "headers": {"Cookie": "x=1"}},
            {"domains": ["multi1.com", "multi2.com"], "headers": {"Referer": "r"}},
        ]
        result = _rules_to_dict(entries)
        assert len(result) == 3

    def test_invalid_entries_skipped(self):
        entries = ["not a dict", {"no_headers": True}, {"domain": "ok.com", "headers": "not a dict"}]
        result = _rules_to_dict(entries)
        assert result == {}

    def test_empty_list(self):
        assert _rules_to_dict([]) == {}


class TestLoadRules:
    """Test rule loading and merging."""

    def test_loads_default_rules(self):
        clear_cache()
        rules = load_rules(force=True)
        # Default rules should have NYT, Wired, etc.
        assert "www.nytimes.com" in rules
        assert "www.wired.com" in rules
        assert "Referer" in rules["www.wired.com"]

    def test_caching(self):
        clear_cache()
        r1 = load_rules()
        r2 = load_rules()
        assert r1 is r2  # Same object (cached)

    def test_force_reload(self):
        clear_cache()
        r1 = load_rules()
        r2 = load_rules(force=True)
        assert r1 is not r2  # Different objects

    def test_nyt_no_googlebot_ua(self):
        """NYT rules should not include Googlebot UA (triggers DataDome)."""
        clear_cache()
        rules = load_rules(force=True)
        nyt = rules.get("www.nytimes.com", {})
        assert "Googlebot" not in nyt.get("User-Agent", "")


class TestGetSiteHeaders:
    """Test URL-to-headers lookup."""

    def test_known_domain(self):
        clear_cache()
        headers = get_site_headers("https://www.wired.com/story/test-article")
        assert headers.get("Referer") == "https://www.google.com/"

    def test_unknown_domain(self):
        headers = get_site_headers("https://unknown-site.example.com/page")
        assert headers == {}

    def test_returns_copy(self):
        h1 = get_site_headers("https://www.wired.com/story/a")
        h2 = get_site_headers("https://www.wired.com/story/b")
        assert h1 == h2
        assert h1 is not h2  # Different objects (copy)

    def test_invalid_url(self):
        assert get_site_headers("not a url") == {}


class TestRulesCliCommands:
    """Test rules CLI subcommands."""

    def test_rules_list(self):
        from typer.testing import CliRunner
        from flarecrawl.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["rules", "list"])
        assert result.exit_code == 0
        assert "nytimes" in result.output

    def test_rules_list_json(self):
        from typer.testing import CliRunner
        from flarecrawl.cli import app
        import json
        runner = CliRunner()
        result = runner.invoke(app, ["rules", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "data" in data
        assert "www.nytimes.com" in data["data"]

    def test_rules_show(self):
        from typer.testing import CliRunner
        from flarecrawl.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["rules", "show", "www.wired.com"])
        assert result.exit_code == 0
        assert "Referer" in result.output

    def test_rules_show_unknown(self):
        from typer.testing import CliRunner
        from flarecrawl.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["rules", "show", "unknown.com"])
        assert result.exit_code == 0
        assert "No rules" in result.output

    def test_rules_path(self):
        from typer.testing import CliRunner
        from flarecrawl.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["rules", "path"])
        assert result.exit_code == 0
        assert "default_rules.yaml" in result.output

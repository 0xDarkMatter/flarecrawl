"""Tests for v0.25.0 P3.1: YAML recipe parser + validator."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def make_recipe(tmp_path):
    def _make(content: str) -> Path:
        p = tmp_path / "test_recipe.yml"
        p.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
        return p
    return _make


class TestLoadRecipe:
    def test_minimal_valid(self, make_recipe):
        from flarecrawl.recipe import load_recipe

        path = make_recipe("goto: https://example.com\nsteps: []\n")
        data = load_recipe(path)
        assert data["goto"] == "https://example.com"
        assert data.get("steps") == []

    def test_full_valid(self, make_recipe):
        from flarecrawl.recipe import load_recipe

        path = make_recipe("""
            version: 1
            goto: https://x.com
            browser: local
            headed: true
            steps:
              - wait_for: .ready
              - click: "button.go"
              - wait: 500ms
              - eval: "1 + 1"
        """)
        data = load_recipe(path)
        assert data["browser"] == "local"
        assert len(data["steps"]) == 4

    def test_missing_file(self, tmp_path):
        from flarecrawl.recipe import RecipeError, load_recipe

        with pytest.raises(RecipeError, match="not found"):
            load_recipe(tmp_path / "nonexistent.yml")

    def test_missing_goto(self, make_recipe):
        from flarecrawl.recipe import RecipeError, load_recipe

        with pytest.raises(RecipeError, match="Missing required keys"):
            load_recipe(make_recipe("version: 1\nsteps: []\n"))

    def test_invalid_url(self, make_recipe):
        from flarecrawl.recipe import RecipeError, load_recipe

        with pytest.raises(RecipeError, match="goto must be"):
            load_recipe(make_recipe("goto: not-a-url\n"))

    def test_unknown_top_level_key(self, make_recipe):
        from flarecrawl.recipe import RecipeError, load_recipe

        with pytest.raises(RecipeError, match="Unknown top-level keys"):
            load_recipe(make_recipe("goto: https://x.com\nfoobar: 1\n"))

    def test_unknown_step_kind(self, make_recipe):
        from flarecrawl.recipe import RecipeError, load_recipe

        with pytest.raises(RecipeError, match="unknown step type"):
            load_recipe(make_recipe(
                "goto: https://x.com\n"
                "steps:\n"
                "  - teleport: somewhere\n"
            ))

    def test_step_with_two_keys_rejected(self, make_recipe):
        from flarecrawl.recipe import RecipeError, load_recipe

        with pytest.raises(RecipeError, match="each step is one key/value"):
            load_recipe(make_recipe(
                "goto: https://x.com\n"
                "steps:\n"
                "  - {click: a, fill: b}\n"
            ))

    def test_unsupported_version(self, make_recipe):
        from flarecrawl.recipe import RecipeError, load_recipe

        with pytest.raises(RecipeError, match="Unsupported recipe version"):
            load_recipe(make_recipe("version: 99\ngoto: https://x.com\n"))


class TestParseDuration:
    def test_milliseconds(self):
        from flarecrawl.recipe import _parse_duration
        assert _parse_duration("500ms") == 0.5

    def test_seconds(self):
        from flarecrawl.recipe import _parse_duration
        assert _parse_duration("2s") == 2.0

    def test_minutes(self):
        from flarecrawl.recipe import _parse_duration
        assert _parse_duration("1m") == 60.0

    def test_bare_number_seconds(self):
        from flarecrawl.recipe import _parse_duration
        assert _parse_duration(2) == 2.0
        assert _parse_duration(0.5) == 0.5

    def test_bare_string_seconds(self):
        from flarecrawl.recipe import _parse_duration
        assert _parse_duration("5") == 5.0

    def test_invalid(self):
        from flarecrawl.recipe import RecipeError, _parse_duration
        with pytest.raises(RecipeError):
            _parse_duration("forever")


class TestJournal:
    def test_journal_round_trip(self, tmp_path, make_recipe):
        from flarecrawl.recipe import (
            append_journal,
            clear_journal,
            load_journal,
        )

        recipe = make_recipe("goto: https://x.com\n")

        # Empty initially
        assert load_journal(recipe) == set()

        append_journal(recipe, {"step": 0, "kind": "click", "status": "ok"})
        append_journal(recipe, {"step": 1, "kind": "wait", "status": "ok"})
        append_journal(recipe, {"step": 2, "kind": "fill", "status": "error"})

        completed = load_journal(recipe)
        assert completed == {0, 1}  # error step not counted

        clear_journal(recipe)
        assert load_journal(recipe) == set()

    def test_recipe_id_stable(self, make_recipe):
        from flarecrawl.recipe import recipe_id

        p = make_recipe("goto: https://x.com\n")
        assert recipe_id(p) == recipe_id(p)
        assert len(recipe_id(p)) == 16


class TestForEachAndCaptureDownload:
    """v0.25.1 follow-ups."""

    def test_for_each_in_validator(self, make_recipe):
        from flarecrawl.recipe import load_recipe

        path = make_recipe("""
            goto: https://x.com
            steps:
              - for_each:
                  selector: "[data-row]"
                  max: 5
                  steps:
                    - click: "@current"
                    - wait: 100ms
        """)
        data = load_recipe(path)
        assert data["steps"][0]["for_each"]["selector"] == "[data-row]"
        assert data["steps"][0]["for_each"]["max"] == 5

    def test_capture_download_in_validator(self, make_recipe):
        from flarecrawl.recipe import load_recipe

        path = make_recipe("""
            goto: https://x.com
            steps:
              - capture_download:
                  to: ./out/
              - click: "button.dl"
        """)
        data = load_recipe(path)
        assert data["steps"][0]["capture_download"]["to"] == "./out/"


class TestDryRun:
    def test_dry_run_returns_plan_without_running(self, make_recipe):
        from flarecrawl.recipe import run

        recipe = make_recipe("""
            goto: https://example.com
            browser: cf
            steps:
              - click: "button.x"
              - wait: 500ms
        """)
        summary = run(recipe, dry_run=True)
        assert summary.get("dry_run") is True
        plan_text = "\n".join(summary["plan"])
        assert "https://example.com" in plan_text
        assert "step 0: click" in plan_text
        assert "step 1: wait" in plan_text

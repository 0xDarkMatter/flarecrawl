"""Live tests for design extraction against public websites.

Run: pytest tests/live/test_design_live.py -v -m live
Requires: CF auth configured (flarecrawl auth login)
Note: consumes browser rendering time -- expected and acceptable.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from flarecrawl.cli import app

runner = CliRunner(charset="utf-8")


@pytest.mark.live
class TestDesignExtract:
    """Smoke tests for design extract against well-known sites."""

    def test_design_extract_example_com(self, has_cf_auth, tmp_path):
        """Smoke test: design extract against simplest possible site."""
        out = tmp_path / "design.md"
        result = runner.invoke(app, ["design", "extract", "https://example.com", "-o", str(out)])
        assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output}"
        assert out.exists(), "design.md was not created"
        content = out.read_text(encoding="utf-8")
        assert "Typography" in content or "Colors" in content or "# Design" in content

    def test_design_extract_stripe_has_sections(self, has_cf_auth, tmp_path):
        """Stripe.com has rich design -- verify sections appear."""
        out = tmp_path / "stripe.md"
        result = runner.invoke(app, ["design", "extract", "https://stripe.com", "-o", str(out)])
        assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output}"
        assert out.exists(), "stripe.md was not created"
        content = out.read_text(encoding="utf-8")
        sections = ["Typography", "Colors", "Spacing", "Borders", "Shadows"]
        found = sum(1 for s in sections if s in content)
        assert found >= 3, f"Only found {found}/5 expected sections. Content:\n{content[:1000]}"

    def test_design_coherence_returns_scores(self, has_cf_auth):
        """Coherence command returns numeric scores, not all zeros."""
        result = runner.invoke(app, ["design", "coherence", "https://stripe.com", "--json"])
        assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output}"
        data = json.loads(result.output)
        assert "data" in data
        coherence = data["data"].get("coherence", {})
        # Categories are nested dicts: {color: {score: N, note: "..."}, ...}
        categories = coherence.get("categories", {})
        scores = [v["score"] for v in categories.values() if isinstance(v, dict) and "score" in v]
        assert coherence.get("overall", 0) > 0 or any(s > 0 for s in scores), \
            f"All scores zero -- EXTRACT_JS likely broken: {coherence}"

    def test_design_extract_preview_html(self, has_cf_auth, tmp_path):
        """Preview generates valid HTML."""
        out = tmp_path / "preview.html"
        result = runner.invoke(app, ["design", "extract", "https://example.com", "--preview", "-o", str(out)])
        assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output}"
        assert out.exists()
        html = out.read_text(encoding="utf-8")
        assert html.strip().startswith("<!DOCTYPE html") or html.strip().startswith("<html")

    def test_design_diff_two_sites(self, has_cf_auth):
        """Diff stripe vs example.com returns structured comparison."""
        result = runner.invoke(app, ["design", "diff", "https://stripe.com", "https://example.com", "--json"])
        assert result.exit_code == 0, f"Exit {result.exit_code}: {result.output}"
        data = json.loads(result.output)
        assert "data" in data

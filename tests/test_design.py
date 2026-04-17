"""Tests for the design extraction module and CLI commands."""

import pytest
from typer.testing import CliRunner

from flarecrawl.cli import app

runner = CliRunner()

# Known HTML with predictable design tokens
FIXTURE_HTML = """
<html>
<head>
<style>
:root {
    --color-primary: #2563eb;
    --color-bg: #ffffff;
    --color-text: #1e293b;
    --radius-sm: 4px;
    --radius-md: 8px;
}
body { font-family: Inter, sans-serif; font-size: 16px; color: var(--color-text); background: var(--color-bg); }
h1 { font-size: 48px; font-weight: 700; line-height: 1.2; }
h2 { font-size: 32px; font-weight: 600; line-height: 1.3; }
h3 { font-size: 24px; font-weight: 600; }
p { margin: 16px 0; line-height: 1.6; }
.btn { padding: 8px 16px; background: var(--color-primary); color: white; border-radius: var(--radius-sm); border: none; }
.card { padding: 24px; border-radius: var(--radius-md); box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
input { padding: 8px 12px; border: 1px solid #e2e8f0; border-radius: var(--radius-sm); }
</style>
</head>
<body>
<h1>Test Page</h1>
<h2>Subtitle</h2>
<p>Body text content.</p>
<button class="btn">Click me</button>
<div class="card"><h3>Card Title</h3><p>Card content</p></div>
<input type="text" placeholder="Input field">
</body>
</html>
"""


class TestWCAGContrast:
    """Test WCAG 2.1 contrast ratio calculation."""

    def test_black_on_white(self):
        from flarecrawl.design import wcag_contrast

        result = wcag_contrast("#000000", "#ffffff")
        assert result["ratio"] >= 21.0
        assert result["aa"] is True
        assert result["aaa"] is True

    def test_white_on_black(self):
        from flarecrawl.design import wcag_contrast

        result = wcag_contrast("#ffffff", "#000000")
        assert result["ratio"] >= 21.0
        assert result["aa"] is True
        assert result["aaa"] is True

    def test_low_contrast(self):
        from flarecrawl.design import wcag_contrast

        result = wcag_contrast("#cccccc", "#ffffff")
        assert result["aa"] is False

    def test_same_color(self):
        from flarecrawl.design import wcag_contrast

        result = wcag_contrast("#ff0000", "#ff0000")
        assert result["ratio"] == 1.0
        assert result["aa"] is False
        assert result["aaa"] is False

    def test_short_hex(self):
        from flarecrawl.design import wcag_contrast

        result = wcag_contrast("#000", "#fff")
        assert result["ratio"] >= 21.0

    def test_aa_threshold(self):
        """Dark gray on white should pass AA but might not pass AAA."""
        from flarecrawl.design import wcag_contrast

        result = wcag_contrast("#767676", "#ffffff")
        assert result["aa"] is True


class TestProcessTokens:
    """Test token processing and normalization."""

    def test_rgb_to_hex_conversion(self):
        from flarecrawl.design import process_tokens

        raw = {
            "colors": [
                {"property": "background-color", "value": "rgb(255, 255, 255)", "count": 10, "context": "body"},
                {"property": "background-color", "value": "rgb(0, 0, 0)", "count": 5, "context": "div"},
                {"property": "color", "value": "rgb(30, 41, 59)", "count": 20, "context": "p"},
            ],
            "cssVars": {},
            "typography": {},
            "spacing": [],
            "radii": [],
            "shadows": [],
            "gradients": [],
            "zIndex": [],
            "transitions": [],
            "layout": {},
            "mediaQueries": [],
            "components": {},
            "svgIcons": [],
            "fontFiles": [],
            "imagePatterns": [],
        }
        tokens = process_tokens(raw)
        # RGB values should be converted to hex
        bg_colors = tokens["colors"]["by_role"].get("background", [])
        bg_hexes = [c["hex"] for c in bg_colors]
        assert "#ffffff" in bg_hexes
        assert "#000000" in bg_hexes
        text_colors = tokens["colors"]["by_role"].get("text", [])
        assert text_colors[0]["hex"] == "#1e293b"

    def test_hex_passthrough(self):
        from flarecrawl.design import process_tokens

        raw = {
            "colors": [
                {"property": "background-color", "value": "#2563eb", "count": 5, "context": "btn"},
            ],
            "cssVars": {},
            "typography": {},
            "spacing": [],
            "radii": [],
            "shadows": [],
            "gradients": [],
            "zIndex": [],
            "transitions": [],
            "layout": {},
            "mediaQueries": [],
            "components": {},
            "svgIcons": [],
            "fontFiles": [],
            "imagePatterns": [],
        }
        tokens = process_tokens(raw)
        bg_colors = tokens["colors"]["by_role"].get("background", [])
        assert bg_colors[0]["hex"] == "#2563eb"

    def test_preserves_other_fields(self):
        from flarecrawl.design import process_tokens

        raw = {
            "colors": [],
            "cssVars": {"--color-primary": "#2563eb"},
            "typography": {"h1": {"fontSize": "48px"}},
            "spacing": [
                {"value": "4px", "count": 5},
                {"value": "8px", "count": 3},
                {"value": "16px", "count": 2},
            ],
            "radii": [{"value": "4px", "count": 2}, {"value": "8px", "count": 1}],
            "shadows": [],
            "gradients": [],
            "zIndex": [],
            "transitions": [],
            "layout": {"grid": [{"gridTemplateColumns": "1fr 1fr", "gap": "8px", "context": "div"}] * 3, "flex": [], "containerWidths": []},
            "mediaQueries": ["(min-width: 768px)"],
            "components": {},
            "svgIcons": [],
            "fontFiles": [],
            "imagePatterns": [],
        }
        tokens = process_tokens(raw)
        assert tokens["cssVars"]["--color-primary"] == "#2563eb"
        assert len(tokens["spacing"]["values"]) == 3
        assert tokens["layout"]["grid_count"] == 3


class TestScoreCoherence:
    """Test design coherence scoring."""

    def test_returns_all_categories(self):
        from flarecrawl.design import score_coherence

        mock_tokens = {
            "colors": {"backgrounds": [("#fff", 50)], "text": [("#000", 30)]},
            "cssVars": {"--a": "1", "--b": "2"},
            "typography": {"h1": {"fontSize": "48px"}, "body": {"fontSize": "16px"}},
            "spacing": {"values": [4, 8, 16, 24, 32]},
            "radii": {"values": [4, 8]},
            "shadows": {"tiers": {"sm": 1, "md": 1, "lg": 1}},
            "gradients": [],
            "zIndex": {"layers": []},
            "layout": {"gridCount": 5, "flexCount": 20, "containerWidths": [1200]},
            "mediaQueries": {"breakpoints": [768, 1024, 1280]},
        }
        result = score_coherence(mock_tokens)
        assert "overall" in result
        assert "grade" in result
        assert "categories" in result
        assert "issues" in result
        assert len(result["categories"]) == 9
        assert 0 <= result["overall"] <= 100

    def test_grade_is_valid(self):
        from flarecrawl.design import score_coherence

        mock_tokens = {
            "colors": {"backgrounds": [("#fff", 50)], "text": [("#000", 30)]},
            "cssVars": {"--a": "1"},
            "typography": {"h1": {"fontSize": "48px"}},
            "spacing": {"values": [4, 8]},
            "radii": {"values": [4]},
            "shadows": {"tiers": {"sm": 1}},
            "gradients": [],
            "zIndex": {"layers": []},
            "layout": {"gridCount": 1},
            "mediaQueries": {"breakpoints": []},
        }
        result = score_coherence(mock_tokens)
        valid_grades = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"]
        assert result["grade"] in valid_grades

    def test_well_designed_site_scores_high(self):
        from flarecrawl.design import score_coherence

        mock_tokens = {
            "colors": {"backgrounds": [("#fff", 50), ("#f5f5f5", 20)], "text": [("#000", 100)]},
            "cssVars": {f"--var-{i}": str(i) for i in range(15)},
            "typography": {"h1": {}, "h2": {}, "h3": {}, "p": {}, "body": {}},
            "spacing": {"values": [4, 8, 12, 16, 24, 32, 48, 64]},
            "radii": {"values": [4, 8]},
            "shadows": {"tiers": {"sm": 5, "md": 3, "lg": 1}},
            "gradients": [],
            "zIndex": {"layers": [1, 10, 100]},
            "layout": {"gridCount": 10, "flexCount": 30},
            "mediaQueries": {"breakpoints": [640, 768, 1024, 1280]},
        }
        result = score_coherence(mock_tokens)
        assert result["overall"] >= 80
        assert result["grade"] in ("A+", "A", "A-", "B+")

    def test_poor_design_scores_low(self):
        from flarecrawl.design import score_coherence

        # 40 unique orphan colors (each used once) => terrible color score
        # Many spacing outliers, many radii, no shadows, no typography => very poor
        mock_tokens = {
            "colors": {
                "all": [{"hex": f"#{i:02x}0000", "role": "background", "count": 1, "context": ""} for i in range(40)],
                "by_role": {
                    "background": [{"hex": f"#{i:02x}0000", "role": "background", "count": 1, "context": ""} for i in range(40)],
                },
                "orphan_count": 40,
            },
            "cssVars": {},
            "typography": {"elements": {}, "scale": [], "modular_ratio": None, "font_families": ["a", "b", "c", "d", "e"]},
            "spacing": {"values": [], "unique": [3, 7, 11, 13, 17, 19, 23], "base_unit": 4, "outliers": [3, 7, 11, 13, 17, 19, 23]},
            "radii": [{"value": f"{i}px", "count": 1} for i in range(20)],
            "shadows": {},
            "gradients": [],
            "zIndex": {"layers": [], "wars": [], "gaps": []},
            "transitions": [],
            "layout": {"grid_count": 0, "flex_count": 0, "grid_templates": [], "flex_items": [], "container_widths": list(range(10))},
            "mediaQueries": [],
            "components": {},
            "svgIcons": {"count": 0, "outline": 0, "solid": 0, "items": []},
            "fontFiles": [],
            "imagePatterns": [],
        }
        result = score_coherence(mock_tokens)
        assert result["overall"] < 60
        assert len(result["issues"]) > 0

    def test_issues_populated(self):
        from flarecrawl.design import score_coherence

        # 50 unique orphan colors => terrible color score, should produce issues
        mock_tokens = {
            "colors": {
                "all": [{"hex": f"#{i:06x}", "role": "background", "count": 1, "context": ""} for i in range(50)],
                "by_role": {
                    "background": [{"hex": f"#{i:06x}", "role": "background", "count": 1, "context": ""} for i in range(50)],
                },
                "orphan_count": 50,
            },
            "cssVars": {},
            "typography": {"elements": {}, "scale": [], "modular_ratio": None, "font_families": []},
            "spacing": {"values": [], "unique": [], "base_unit": 4, "outliers": []},
            "radii": [],
            "shadows": {},
            "gradients": [],
            "zIndex": {"layers": [], "wars": [], "gaps": []},
            "transitions": [],
            "layout": {"grid_count": 0, "flex_count": 0, "grid_templates": [], "flex_items": [], "container_widths": []},
            "mediaQueries": [],
            "components": {},
            "svgIcons": {"count": 0, "outline": 0, "solid": 0, "items": []},
            "fontFiles": [],
            "imagePatterns": [],
        }
        result = score_coherence(mock_tokens)
        assert any("color" in issue.lower() for issue in result["issues"])


class TestFormatDesignMd:
    """Test markdown output formatting."""

    def test_has_all_sections(self):
        from flarecrawl.design import format_design_md

        tokens = {
            "colors": {
                "all": [
                    {"hex": "#ffffff", "role": "background", "count": 50, "context": "body"},
                    {"hex": "#f5f5f5", "role": "background", "count": 10, "context": "div"},
                    {"hex": "#000000", "role": "text", "count": 30, "context": "p"},
                ],
                "by_role": {
                    "background": [
                        {"hex": "#ffffff", "role": "background", "count": 50, "context": "body"},
                        {"hex": "#f5f5f5", "role": "background", "count": 10, "context": "div"},
                    ],
                    "text": [{"hex": "#000000", "role": "text", "count": 30, "context": "p"}],
                },
                "orphan_count": 0,
            },
            "cssVars": {"--color-primary": "#2563eb"},
            "typography": {
                "elements": {"h1": {"fontSize": "48px", "fontWeight": "700", "lineHeight": "1.2", "fontFamily": "Inter"}},
                "scale": [48.0],
                "modular_ratio": None,
                "font_families": ["Inter"],
            },
            "spacing": {"values": [(4.0, 5), (8.0, 3), (16.0, 2), (24.0, 1)], "unique": [4.0, 8.0, 16.0, 24.0], "base_unit": 4, "outliers": []},
            "radii": [{"value": "4px", "count": 2}, {"value": "8px", "count": 1}],
            "shadows": {"sm": ["0 1px 2px rgba(0,0,0,0.1)"], "md": ["0 4px 6px rgba(0,0,0,0.1)"]},
            "gradients": [],
            "zIndex": {"layers": [], "wars": [], "gaps": []},
            "transitions": [],
            "layout": {"grid_count": 5, "flex_count": 10, "grid_templates": [], "flex_items": [], "container_widths": []},
            "mediaQueries": ["(min-width: 768px)", "(min-width: 1024px)"],
            "components": {},
            "svgIcons": {"count": 0, "outline": 0, "solid": 0, "items": []},
            "fontFiles": [],
            "imagePatterns": [],
        }
        coherence = {
            "overall": 80,
            "grade": "B+",
            "categories": {"colors": {"score": 90, "note": "3 unique colors, 0 orphans."}},
            "issues": [],
        }
        md = format_design_md(tokens, coherence, "https://example.com")
        assert "# Design System:" in md
        assert "## Design Coherence" in md
        assert "## Color Palette" in md
        assert "## Typography" in md
        assert "## Quick Start" in md
        assert "B+" in md
        assert "80" in md

    def test_contains_url(self):
        from flarecrawl.design import format_design_md

        tokens = {
            "colors": {"all": [], "by_role": {}, "orphan_count": 0},
            "cssVars": {},
            "typography": {"elements": {}, "scale": [], "modular_ratio": None, "font_families": []},
            "spacing": {"values": [], "unique": [], "base_unit": 4, "outliers": []},
            "radii": [],
            "shadows": {},
            "gradients": [],
            "zIndex": {"layers": [], "wars": [], "gaps": []},
            "transitions": [],
            "layout": {"grid_count": 0, "flex_count": 0, "grid_templates": [], "flex_items": [], "container_widths": []},
            "mediaQueries": [],
            "components": {},
            "svgIcons": {"count": 0, "outline": 0, "solid": 0, "items": []},
            "fontFiles": [],
            "imagePatterns": [],
        }
        coherence = {"overall": 50, "grade": "D", "categories": {}, "issues": []}
        md = format_design_md(tokens, coherence, "https://test.example.com")
        assert "https://test.example.com" in md


class TestFormatPreviewHtml:
    """Test HTML preview output."""

    def test_is_valid_html(self):
        from flarecrawl.design import format_preview_html

        tokens = {
            "colors": {
                "all": [
                    {"hex": "#ffffff", "role": "background", "count": 50, "context": "body"},
                    {"hex": "#000000", "role": "text", "count": 30, "context": "p"},
                ],
                "by_role": {
                    "background": [{"hex": "#ffffff", "role": "background", "count": 50, "context": "body"}],
                    "text": [{"hex": "#000000", "role": "text", "count": 30, "context": "p"}],
                },
                "orphan_count": 0,
            },
            "cssVars": {},
            "typography": {
                "elements": {"h1": {"fontSize": "48px", "fontWeight": "700", "lineHeight": "1.2"}},
                "scale": [48.0],
                "modular_ratio": None,
                "font_families": [],
            },
            "spacing": {"values": [], "unique": [], "base_unit": 4, "outliers": []},
            "radii": [],
            "shadows": {},
            "gradients": [],
            "zIndex": {"layers": [], "wars": [], "gaps": []},
            "transitions": [],
            "layout": {"grid_count": 0, "flex_count": 0, "grid_templates": [], "flex_items": [], "container_widths": []},
            "mediaQueries": [],
            "components": {},
            "svgIcons": {"count": 0, "outline": 0, "solid": 0, "items": []},
            "fontFiles": [],
            "imagePatterns": [],
        }
        coherence = {"overall": 75, "grade": "B", "categories": {}, "issues": []}
        html = format_preview_html(tokens, coherence, "https://example.com")
        assert "<!DOCTYPE html>" in html
        assert "example.com" in html
        assert "B" in html
        assert "swatch" in html


class TestDesignCLI:
    """Test design CLI command registration and help."""

    def test_design_in_help(self):
        result = runner.invoke(app, ["--help"])
        assert "design" in result.output

    def test_design_extract_in_help(self):
        result = runner.invoke(app, ["design", "extract", "--help"])
        assert result.exit_code == 0
        assert "--preview" in result.output
        assert "--full" in result.output
        assert "--dark" in result.output
        assert "--responsive" in result.output
        assert "--interactions" in result.output
        assert "--session" in result.output
        assert "--proxy" in result.output
        assert "--output" in result.output
        assert "--json" in result.output
        assert "--depth" in result.output
        assert "--keep-alive" in result.output
        assert "--auto-dark" in result.output

    def test_design_coherence_in_help(self):
        result = runner.invoke(app, ["design", "coherence", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output
        assert "--proxy" in result.output
        assert "--session" in result.output

    def test_design_diff_in_help(self):
        result = runner.invoke(app, ["design", "diff", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output
        assert "--output" in result.output
        assert "--proxy" in result.output

    def test_design_group_help(self):
        result = runner.invoke(app, ["design", "--help"])
        assert result.exit_code == 0
        assert "extract" in result.output
        assert "coherence" in result.output
        assert "diff" in result.output


class TestExtractJS:
    """Test that EXTRACT_JS is a valid string constant."""

    def test_extract_js_is_string(self):
        from flarecrawl.design import EXTRACT_JS

        assert isinstance(EXTRACT_JS, str)
        assert len(EXTRACT_JS) > 100

    def test_extract_js_is_iife(self):
        from flarecrawl.design import EXTRACT_JS

        assert EXTRACT_JS.strip().startswith("(")
        assert EXTRACT_JS.strip().endswith(")")


class TestRgbToHex:
    """Test internal color conversion helper."""

    def test_rgb_format(self):
        from flarecrawl.design import _rgb_to_hex

        assert _rgb_to_hex("rgb(255, 0, 0)") == "#ff0000"
        assert _rgb_to_hex("rgb(0, 128, 255)") == "#0080ff"

    def test_rgba_format(self):
        from flarecrawl.design import _rgb_to_hex

        assert _rgb_to_hex("rgba(255, 255, 255, 1)") == "#ffffff"

    def test_hex_passthrough(self):
        from flarecrawl.design import _rgb_to_hex

        assert _rgb_to_hex("#2563eb") == "#2563eb"
        assert _rgb_to_hex("#AABBCC") == "#aabbcc"

    def test_unknown_format(self):
        from flarecrawl.design import _rgb_to_hex

        assert _rgb_to_hex("hsl(200, 50%, 50%)") == "hsl(200, 50%, 50%)"

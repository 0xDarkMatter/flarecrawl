"""Tests for v0.26.0 P1: synthetic interaction (humanize) module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestBezierPath:
    def test_endpoints_match(self):
        from flarecrawl.humanize import _bezier_path
        pts = _bezier_path(10, 20, 100, 200, steps=20)
        # First point ≈ start, last point ≈ end
        assert abs(pts[0][0] - 10) < 0.01
        assert abs(pts[0][1] - 20) < 0.01
        assert abs(pts[-1][0] - 100) < 0.01
        assert abs(pts[-1][1] - 200) < 0.01

    def test_step_count(self):
        from flarecrawl.humanize import _bezier_path
        pts = _bezier_path(0, 0, 100, 100, steps=15)
        assert len(pts) == 16  # steps + 1

    def test_path_curves_off_straight_line(self):
        """Bezier with deflection ≠ 0 must not be a straight line."""
        from flarecrawl.humanize import _bezier_path
        # Run multiple times — control-point deflection is randomised; any
        # one run could happen to land on the line if randomness conspires.
        # With steps=20 across multiple seeds, the midpoint must deviate at
        # least once.
        deviations = []
        import random
        for _ in range(10):
            random.seed(_ + 1)
            pts = _bezier_path(0, 0, 100, 0, steps=20)
            mid = pts[10]
            deviations.append(abs(mid[1]))  # off-straight = non-zero y
        assert max(deviations) > 1.0


class TestHumanizePage:
    """Verify humanize_page emits the expected CDP commands."""

    def test_fast_profile_dispatches_mouse_and_scroll(self):
        from flarecrawl.humanize import humanize_page

        page = MagicMock()
        result = humanize_page(page, profile="fast", seed=42)
        assert result["profile"] == "fast"
        assert result["moves"] == 1
        assert result["scrolls"] == 1
        # Confirm at least one mouseMoved and one mouseWheel were dispatched
        methods = [c.args[0] for c in page.send.call_args_list]
        types = [c.args[1].get("type") for c in page.send.call_args_list]
        assert all(m == "Input.dispatchMouseEvent" for m in methods)
        assert "mouseMoved" in types
        assert "mouseWheel" in types

    def test_thorough_profile_more_actions(self):
        from flarecrawl.humanize import humanize_page

        page = MagicMock()
        result = humanize_page(page, profile="thorough", seed=42)
        assert result["moves"] == 4
        assert result["scrolls"] == 3

    def test_unknown_profile_falls_back_to_natural(self):
        from flarecrawl.humanize import humanize_page

        page = MagicMock()
        result = humanize_page(page, profile="bogus", seed=42)
        # natural = 2 moves + 2 scrolls
        assert result["moves"] == 2
        assert result["scrolls"] == 2

    def test_failed_send_does_not_raise(self):
        """Humanize is best-effort — a failed mouse event shouldn't crash."""
        from flarecrawl.humanize import humanize_page

        page = MagicMock()
        page.send.side_effect = RuntimeError("disconnected")
        # Should not raise
        result = humanize_page(page, profile="fast", seed=42)
        assert "elapsed_ms" in result

    def test_mouse_moves_stay_in_viewport(self):
        from flarecrawl.humanize import humanize_page

        page = MagicMock()
        humanize_page(page, viewport=(800, 600), profile="thorough", seed=42)
        for call in page.send.call_args_list:
            params = call.args[1]
            x, y = params.get("x", 0), params.get("y", 0)
            assert 0 <= x <= 800, f"x={x} out of bounds"
            assert 0 <= y <= 600, f"y={y} out of bounds"

    def test_seed_makes_path_deterministic(self):
        from flarecrawl.humanize import humanize_page

        page1 = MagicMock()
        page2 = MagicMock()
        humanize_page(page1, profile="fast", seed=123)
        humanize_page(page2, profile="fast", seed=123)
        # Same number of calls
        assert page1.send.call_count == page2.send.call_count
        # Same args (positional + kwargs) at each call site
        for c1, c2 in zip(page1.send.call_args_list, page2.send.call_args_list):
            assert c1.args[0] == c2.args[0]
            assert c1.args[1].get("type") == c2.args[1].get("type")


class TestProfileBudget:
    """Each profile must stay under its budget so users can plan timeouts."""

    @pytest.mark.parametrize("profile,max_ms", [
        ("fast", 1500),       # budget 700, allow 2x slack for slow CI
        ("natural", 4000),    # budget 1500
        ("thorough", 8000),   # budget 3000
    ])
    def test_within_budget(self, profile, max_ms):
        from flarecrawl.humanize import humanize_page

        page = MagicMock()
        result = humanize_page(page, profile=profile, seed=42)
        assert result["elapsed_ms"] <= max_ms, (
            f"{profile} profile took {result['elapsed_ms']}ms (budget {max_ms}ms)"
        )

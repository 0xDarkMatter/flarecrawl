"""Unit tests for the agent guide surface (Layer 1)."""

from __future__ import annotations

import pytest

from flarecrawl import guide

SAMPLE = """# Flarecrawl - AI Agent Context

One-line intro paragraph.

## Quick Reference

| Task | Command |
|------|---------|
| Scrape | `flarecrawl scrape URL` |

## Command Details

Intro prose for command details.

### scrape

scrape body text.

### p6 — mint→replay anti-bot primitive (v0.28.0)

p6 body text.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | success |

## Agent Rules

1. first rule
"""


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(guide, "load_guide", lambda: SAMPLE)


class TestSlug:
    def test_strips_version_paren_and_arrows(self):
        assert guide._slug("p6 — mint→replay anti-bot primitive (v0.28.0)") == \
            "p6-mint-replay-anti-bot-primitive"

    def test_basic(self):
        assert guide._slug("Exit Codes") == "exit-codes"
        assert guide._slug("Quick Reference") == "quick-reference"


class TestParseSections:
    def test_finds_h2_and_h3(self, patched):
        secs = guide.parse_sections(SAMPLE)
        slugs = [s.slug for s in secs]
        assert "quick-reference" in slugs
        assert "command-details" in slugs
        assert "scrape" in slugs
        assert "exit-codes" in slugs

    def test_h2_carries_full_subtree(self, patched):
        # `guide command-details` is the full command reference: intro
        # prose AND every child command, up to the next H2.
        secs = {s.slug: s for s in guide.parse_sections(SAMPLE)}
        cd = secs["command-details"]
        assert "Intro prose for command details." in cd.body
        assert "scrape body text." in cd.body
        assert "p6 body text." in cd.body
        # ...but not the *next* H2's content.
        assert "success" not in cd.body  # that's in Exit Codes

    def test_h3_body_stops_at_next_h3(self, patched):
        secs = {s.slug: s for s in guide.parse_sections(SAMPLE)}
        scrape = secs["scrape"]
        assert "scrape body text." in scrape.body
        assert "p6 body text." not in scrape.body  # no bleed into next H3

    def test_levels(self, patched):
        secs = {s.slug: s for s in guide.parse_sections(SAMPLE)}
        assert secs["quick-reference"].level == 2
        assert secs["scrape"].level == 3


class TestResolveTopic:
    def test_exact_slug(self, patched):
        s = guide.resolve_topic("exit-codes")
        assert s is not None and s.slug == "exit-codes"

    def test_alias_hard_targets_to_p6(self, patched):
        s = guide.resolve_topic("hard-targets")
        assert s is not None
        assert "p6 body text." in s.body

    def test_alias_errors_to_exit_codes(self, patched):
        s = guide.resolve_topic("errors")
        assert s is not None and s.slug == "exit-codes"

    def test_alias_rules(self, patched):
        s = guide.resolve_topic("rules")
        assert s is not None and s.slug == "agent-rules"

    def test_prefix_match(self, patched):
        s = guide.resolve_topic("exit")
        assert s is not None and s.slug == "exit-codes"

    def test_substring_match(self, patched):
        s = guide.resolve_topic("command")
        assert s is not None and s.slug == "command-details"

    def test_unknown_returns_none(self, patched):
        assert guide.resolve_topic("nonexistent-zzz") is None

    def test_case_insensitive(self, patched):
        assert guide.resolve_topic("Exit-Codes") is not None


class TestOverview:
    def test_includes_intro_and_quickref_and_topics(self, patched):
        out = guide.overview()
        assert "One-line intro paragraph." in out
        assert "Quick Reference" in out
        assert "## Guide topics" in out
        assert "exit-codes" in out
        # Quick Reference itself should not also be listed as a topic.
        topics_block = out.split("## Guide topics")[1]
        assert "quick-reference" not in topics_block


class TestListTopics:
    def test_returns_slug_title_pairs(self, patched):
        rows = guide.list_topics()
        slugs = [r[0] for r in rows]
        assert "scrape" in slugs
        assert ("exit-codes", "Exit Codes") in rows


class TestRealGuidePackaged:
    """Against the real AGENTS.md — proves packaging/loader works."""

    def test_guide_path_resolves(self):
        p = guide.guide_path()
        assert p.is_file()
        assert p.name == "AGENTS.md"

    def test_real_sections_parse(self):
        secs = guide.parse_sections(guide.load_guide())
        slugs = {s.slug for s in secs}
        assert "quick-reference" in slugs
        assert "agent-rules" in slugs

    def test_real_hard_targets_alias(self):
        s = guide.resolve_topic("hard-targets")
        assert s is not None
        assert "p6" in s.slug or "p6" in s.title.lower()

    def test_overview_nonempty(self):
        assert len(guide.overview()) > 200

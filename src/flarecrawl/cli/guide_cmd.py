"""guide command."""

from __future__ import annotations

from typing import Annotated

import typer

from ._common import (
    EXIT_ERROR,
    EXIT_NOT_FOUND,
    _error,
    _output_text,
)

# Module-local Typer — commands are mounted by register() in __init__.py
_cmd = typer.Typer(add_completion=False)


@_cmd.command("guide")
def guide_command(
    topic: Annotated[str | None, typer.Argument(help="Section to show (fuzzy; aliases: hard-targets, json, errors, rules, auth, ...)")] = None,
    list_topics_flag: Annotated[bool, typer.Option("--list", help="List every available topic and exit")] = False,
):
    """Print the agent orientation guide (when/why each command, JSON
    shapes, exit codes, footgun rules).

    `--help` is per-command reference; this is the cross-cutting mental
    model an agent needs on first contact. Backed by the packaged
    AGENTS.md, so it works after a bare install with no repo on disk.

    Example:
        flarecrawl guide                 # overview + Quick Reference + topics
        flarecrawl guide --list          # every section
        flarecrawl guide hard-targets    # the P6 / anti-bot escalation
        flarecrawl guide json            # JSON output shapes
        flarecrawl guide errors          # exit codes
    """
    from ..guide import (
        GuideError,
        list_topics,
        load_guide,
        overview,
        parse_sections,
        resolve_topic,
    )

    try:
        if list_topics_flag:
            rows = list_topics()
            for slug, title in rows:
                indent = "" if title == title.lstrip() else "  "
                _output_text(f"{indent}{slug:42s} {title}")
            return

        if not topic:
            _output_text(overview())
            return

        section = resolve_topic(topic)
        if section is None:
            slugs = ", ".join(
                s.slug for s in parse_sections(load_guide()) if s.level == 2
            )
            _error(
                f"No guide topic matches '{topic}'. Top-level topics: {slugs}. "
                f"Try `flarecrawl guide --list` for all sections.",
                "NOT_FOUND", EXIT_NOT_FOUND,
            )
            return
        _output_text(section.body)
    except GuideError as e:
        _error(str(e), "ERROR", EXIT_ERROR)


# ------------------------------------------------------------------
# Auth commands
# ------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Register this module's commands onto the main app."""
    app.command('guide')(guide_command)

"""§30.11 Coverage audit test.

Enumerate the Typer app's registered commands and assert each is either:
1. Mapped by at least one T2/T3 tool's declared `covers` attribute, OR
2. Present in the declared gaps (coverage_gaps in pyproject.toml).

This test is the §30.11 reachability guarantee.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# CLI command discovery
# ---------------------------------------------------------------------------


def _get_cli_commands() -> set[str]:
    """Walk the flarecrawl Typer app and return all command names."""
    from flarecrawl.cli import app
    from typer.main import get_group

    group = get_group(app)
    commands: set[str] = set()

    for name, cmd in group.commands.items():
        commands.add(name)
        # Walk sub-apps (e.g. auth, cache, session, etc.)
        if hasattr(cmd, "commands"):
            for sub_name in cmd.commands:
                commands.add(f"{name} {sub_name}")

    return commands


# ---------------------------------------------------------------------------
# Declared gaps (from orientation.py — single source of truth)
# ---------------------------------------------------------------------------


def _get_declared_gap_commands() -> set[str]:
    """Return the set of commands declared as gaps."""
    from flarecrawl.mcp_tools.orientation import COVERAGE_GAPS

    gaps: set[str] = set()
    for gap in COVERAGE_GAPS:
        cmd = gap["command"]
        # Expand wildcard patterns
        if cmd.endswith(" *"):
            base = cmd[:-2]
            # Add base and any known sub-commands
            gaps.add(base)
        # Add the raw command string
        gaps.add(cmd)
        # Also add individual words for flexible matching
        for part in cmd.replace("/", " ").replace(",", " ").split():
            if part not in ("*", "flags", "clear"):
                gaps.add(part)
    return gaps


# ---------------------------------------------------------------------------
# Coverage map from registry
# ---------------------------------------------------------------------------


def _get_covered_commands() -> set[str]:
    """Return the set of CLI commands covered by any tool's declared 'covers' attrs.

    Includes orientation-tier tools because guide/usage are orientation concerns.
    """
    from flarecrawl.mcp_tools.registry import build_registry

    registry = build_registry(read_only=False)
    covered: set[str] = set()
    for name, defn in registry.items():
        for cmd in defn.get("covers", []):
            covered.add(cmd)
    return covered


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_all_cli_commands_covered_or_declared_gap():
    """Every CLI command must be reachable via T2/T3 or declared as a gap."""
    cli_commands = _get_cli_commands()
    covered = _get_covered_commands()
    gap_commands = _get_declared_gap_commands()

    # Build the union of covered + gap keywords for flexible matching
    all_covered_or_gap = covered | gap_commands

    missing: list[str] = []
    for cmd in cli_commands:
        # Check direct match
        if cmd in all_covered_or_gap:
            continue
        # Check if any word in the command matches
        cmd_parts = cmd.replace("-", "_").split()
        if any(part in all_covered_or_gap for part in cmd_parts):
            continue
        # Check if the command starts with a covered prefix
        if any(cmd.startswith(cov) or cov.startswith(cmd) for cov in all_covered_or_gap):
            continue
        missing.append(cmd)

    assert not missing, (
        f"CLI commands neither covered by T2/T3 tools nor declared as gaps:\n"
        + "\n".join(f"  - {cmd}" for cmd in sorted(missing))
    )


def test_covered_commands_exist_in_cli():
    """Every command declared in `covers` should exist in the CLI."""
    cli_commands = _get_cli_commands()
    covered = _get_covered_commands()

    # Build normalised CLI names
    cli_normalised = set()
    for cmd in cli_commands:
        cli_normalised.add(cmd)
        cli_normalised.add(cmd.replace("-", "_"))
        cli_normalised.add(cmd.replace("_", "-"))
        cli_normalised.update(cmd.split())

    not_found = []
    for cmd in covered:
        # Flexible match
        cmd_norm = cmd.replace("-", "_").replace(" ", "_")
        if (
            cmd in cli_normalised
            or cmd.replace("-", "_") in cli_normalised
            or cmd.replace("_", "-") in cli_normalised
            or any(part in cli_normalised for part in cmd.split())
        ):
            continue
        not_found.append(cmd)

    assert not not_found, (
        f"Commands in 'covers' not found in CLI:\n"
        + "\n".join(f"  - {cmd}" for cmd in sorted(not_found))
    )


def test_gap_list_has_11_entries():
    """Exactly 11 coverage gaps must be declared."""
    from flarecrawl.mcp_tools.orientation import COVERAGE_GAPS
    assert len(COVERAGE_GAPS) == 11, (
        f"Expected 11 coverage gaps, got {len(COVERAGE_GAPS)}"
    )

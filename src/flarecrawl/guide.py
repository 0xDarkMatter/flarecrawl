"""Agent-facing guide surface for Flarecrawl — v0.28.0.

`--help` is reference; it tells an agent which flags exist, not which
command to reach for, how commands compose, or where the footguns are.
That orientation already exists in AGENTS.md — this module makes it
discoverable *from the tool itself* so a first-touch agent (including one
that only `pip install`ed the wheel, with no repo on disk) can read it.

`flarecrawl guide` emits the packaged AGENTS.md; `guide <topic>` scopes
to one section. Single source of truth, zero doc drift: the same file
repo readers see is the file the binary serves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Friendly aliases → a stable lookup key an agent would actually want.
# Agents guess intent words ("hard-targets", "errors"), not exact H2 text.
# Values are resolved through the same exact→prefix→substring matcher, so a
# short stable substring is more robust than a hardcoded full slug (the p6
# heading slug, for instance, depends on how "→" normalises).
_ALIASES: dict[str, str] = {
    "hard-targets": "p6-mint-replay",
    "hard-target": "p6-mint-replay",
    "p6": "p6-mint-replay",
    "antibot": "p6-mint-replay",
    "anti-bot": "p6-mint-replay",
    "blocked": "p6-mint-replay",
    "mint": "p6-mint-replay",
    "replay": "p6-mint-replay",
    "json": "json-output-shapes",
    "schema": "json-output-shapes",
    "output": "json-output-shapes",
    "errors": "exit-codes",
    "error": "exit-codes",
    "exit": "exit-codes",
    "exit-code": "exit-codes",
    "rules": "agent-rules",
    "footguns": "agent-rules",
    "footgun": "agent-rules",
    "auth": "authentication",
    "login": "authentication",
    "commands": "command-details",
    "batch": "batch-parallel",
    "parallel": "batch-parallel",
    "env": "environment-variables",
    "pricing": "pricing-reference",
    "quickref": "quick-reference",
    "tech": "tech-detect",
    "wappalyzer": "tech-detect",
    "fingerprint": "tech-detect",
    "stack": "tech-detect",
    "detect": "tech-detect",
}


class GuideError(Exception):
    """Raised when the packaged guide cannot be located."""


@dataclass(slots=True)
class Section:
    level: int          # 2 for ##, 3 for ###
    title: str          # raw heading text
    slug: str           # normalised lookup key
    body: str           # heading line + content up to the next heading


def _slug(title: str) -> str:
    """Normalise a heading to a lookup key.

    Drops parentheticals/version notes ("(v0.28.0)"), lowercases, and
    collapses non-alphanumerics to single hyphens.
    """
    t = re.sub(r"\([^)]*\)", "", title)          # strip "(v0.28.0)" etc.
    t = t.replace("→", " ").replace("—", " ")
    t = re.sub(r"[^a-z0-9]+", "-", t.lower())
    return t.strip("-")


def guide_path() -> Path:
    """Locate the packaged AGENTS.md.

    Order: packaged resource (wheel install) → repo root walking up from
    this file (editable / source checkout).  Raises GuideError if neither
    is found.
    """
    # 1. Packaged alongside the module (hatch force-include target).
    try:
        from importlib.resources import files as _res_files
        cand = _res_files("flarecrawl").joinpath("AGENTS.md")
        if cand.is_file():
            return Path(str(cand))
    except (ImportError, ModuleNotFoundError, FileNotFoundError, TypeError):
        pass
    # 2. Repo root (editable install / running from source).
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand2 = parent / "AGENTS.md"
        if cand2.is_file():
            return cand2
    raise GuideError(
        "Packaged guide (AGENTS.md) not found. This is a packaging bug; "
        "reinstall flarecrawl or read AGENTS.md in the source repo."
    )


def load_guide() -> str:
    return guide_path().read_text(encoding="utf-8")


def _preamble(text: str) -> str:
    """Everything before the first heading (title + one-line intro)."""
    out: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,3}\s", line) and out:
            break
        out.append(line)
    return "\n".join(out).strip()


def parse_sections(text: str) -> list[Section]:
    """Split the guide into H2/H3 sections.

    A section runs from its heading until the next heading of the same or
    higher level: an H2 therefore carries its whole subtree (intro prose
    *and* its child H3s — `guide command-details` is the full command
    reference), while an H3 stops at the next H3/H2 (`guide scrape` is
    just that command, no bleed into the next).
    """
    lines = text.splitlines()
    heads: list[tuple[int, int, str]] = []  # (line_idx, level, title)
    for i, line in enumerate(lines):
        m = re.match(r"^(#{2,3})\s+(.*)$", line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))

    sections: list[Section] = []
    for n, (idx, level, title) in enumerate(heads):
        end = len(lines)
        for j in range(n + 1, len(heads)):
            if heads[j][1] <= level:
                end = heads[j][0]
                break
        body = "\n".join(lines[idx:end]).strip()
        sections.append(Section(level, title, _slug(title), body))
    return sections


def list_topics() -> list[tuple[str, str]]:
    """(slug, title) for every section, in document order."""
    return [(s.slug, s.title) for s in parse_sections(load_guide())]


def resolve_topic(query: str) -> Section | None:
    """Find the best section for a user query.

    Resolution order: alias table → exact slug → unique prefix → unique
    substring.  Returns None if nothing matches or a prefix/substring is
    ambiguous (caller should then show the topic list).
    """
    sections = parse_sections(load_guide())
    by_slug = {s.slug: s for s in sections}

    q = _slug(query)
    if q in _ALIASES:
        q = _ALIASES[q]
    if q in by_slug:
        return by_slug[q]

    prefix = [s for s in sections if s.slug.startswith(q)]
    if len(prefix) == 1:
        return prefix[0]
    substr = [s for s in sections if q in s.slug]
    if len(substr) == 1:
        return substr[0]
    return None


def overview() -> str:
    """No-topic view: title/intro + Quick Reference + topic index."""
    text = load_guide()
    sections = parse_sections(text)
    parts = [_preamble(text)]
    qref = next((s for s in sections if s.slug == "quick-reference"), None)
    if qref:
        parts.append(qref.body)
    topics = ", ".join(
        s.slug for s in sections if s.level == 2 and s.slug != "quick-reference"
    )
    parts.append(
        "## Guide topics\n\n"
        f"{topics}\n\n"
        "Run `flarecrawl guide <topic>` for any of the above (fuzzy + "
        "aliases like `hard-targets`, `json`, `errors`, `rules`, `auth`), "
        "or `flarecrawl guide --list` for every section."
    )
    return "\n\n".join(p for p in parts if p)

"""Per-site header rulesets for Flarecrawl.

Loads default rules shipped with the package and merges user overrides
from ``~/.config/flarecrawl/rules.yaml``. Rules map domains to HTTP
headers injected during paywall bypass and stealth fetches.

YAML format is compatible with everywall/ladder rulesets.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import yaml

from .config import get_config_dir

# Module-level cache (loaded once per CLI invocation)
_rules_cache: dict[str, dict] | None = None


def _default_rules_path() -> Path:
    """Path to bundled default_rules.yaml."""
    return Path(__file__).parent / "default_rules.yaml"


def _user_rules_path() -> Path:
    """Path to user rules.yaml."""
    return get_config_dir() / "rules.yaml"


def _parse_yaml(path: Path) -> list[dict]:
    """Parse a YAML ruleset file. Returns list of rule entries."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, list) else []


def _rules_to_dict(entries: list[dict]) -> dict[str, dict]:
    """Convert list of rule entries to domain -> headers dict.

    Handles both ``domain: str`` and ``domains: list[str]`` entries.
    """
    result: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        headers = entry.get("headers", {})
        if not isinstance(headers, dict):
            continue
        if "domain" in entry:
            result[entry["domain"]] = dict(headers)
        elif "domains" in entry:
            for d in entry["domains"]:
                result[d] = dict(headers)
    return result


def load_rules(*, force: bool = False) -> dict[str, dict]:
    """Load merged rules (defaults + user overrides). Cached in-process.

    Default rules are shipped with the package. User rules from
    ``~/.config/flarecrawl/rules.yaml`` are merged on top (user wins
    per-domain).

    Args:
        force: Reload from disk, ignoring cache.

    Returns:
        Dict mapping domain -> headers dict.
    """
    global _rules_cache
    if _rules_cache is not None and not force:
        return _rules_cache

    rules: dict[str, dict] = {}

    # Load defaults
    rules.update(_rules_to_dict(_parse_yaml(_default_rules_path())))

    # Load user overrides (merge on top)
    rules.update(_rules_to_dict(_parse_yaml(_user_rules_path())))

    _rules_cache = rules
    return _rules_cache


def get_site_headers(url: str) -> dict:
    """Look up per-site header overrides for a URL.

    Args:
        url: The target URL.

    Returns:
        Dict of headers to inject, or empty dict if no rules match.
    """
    try:
        domain = urlparse(url).netloc
        return dict(load_rules().get(domain, {}))
    except Exception:
        return {}


def list_rules() -> dict[str, dict]:
    """Return all loaded rules (for CLI display)."""
    return dict(load_rules())


def clear_cache() -> None:
    """Clear the rules cache (force reload on next access)."""
    global _rules_cache
    _rules_cache = None

"""Test the MCP tool registry.

Tests:
- Registry importable without mcp installed
- 36 tools present with expected names/tiers
- No stub patterns in any handler source
- Every short_description ≤80 chars and starts with a verb
- T3 names end _raw
- Read-only mode excludes exactly the 5 listed tools
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys

import pytest

# ---------------------------------------------------------------------------
# Registry import (must work without mcp)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry():
    """Build registry without read-only mode."""
    from flarecrawl.mcp_tools.registry import build_registry
    return build_registry(read_only=False)


@pytest.fixture(scope="module")
def registry_ro():
    """Build registry with read-only mode."""
    from flarecrawl.mcp_tools.registry import build_registry
    return build_registry(read_only=True)


# ---------------------------------------------------------------------------
# Import without mcp
# ---------------------------------------------------------------------------


def test_registry_importable_without_mcp(monkeypatch):
    """Registry must import cleanly even when 'mcp' package is not available."""
    # Temporarily hide 'mcp' from the import system
    orig = sys.modules.get("mcp")
    sys.modules["mcp"] = None  # type: ignore[assignment]
    try:
        # Remove cached submodules of flarecrawl.mcp_tools
        to_remove = [k for k in sys.modules if k.startswith("flarecrawl.mcp_tools")]
        for k in to_remove:
            del sys.modules[k]
        # Should not raise
        from flarecrawl.mcp_tools.registry import build_registry as _br  # noqa: F401
        r = _br(read_only=False)
        assert len(r) > 0
    finally:
        if orig is None:
            sys.modules.pop("mcp", None)
        else:
            sys.modules["mcp"] = orig
        # Restore fresh modules
        to_remove = [k for k in sys.modules if k.startswith("flarecrawl.mcp_tools")]
        for k in to_remove:
            del sys.modules[k]


# ---------------------------------------------------------------------------
# Tool count
# ---------------------------------------------------------------------------

EXPECTED_NAMES = {
    # Orientation (5)
    "capabilities", "guide", "diagnostics", "permissions_check", "schema_generate",
    # T1 Composite (5)
    "read_page", "research_web", "site_overview", "extract_data", "check_page_changes",
    # T2 Curated (17)
    "web_search", "fetch_url", "page_links", "urls_discover", "page_schema",
    "page_favicon", "page_screenshot", "page_pdf", "page_interact", "tech_detect",
    "openapi_discover", "crawl_start", "crawl_status", "crawl_results", "site_download",
    "session_list", "session_inspect",
    # T3 Raw (9)
    "scrape_raw", "fetch_raw", "crawl_raw", "extract_raw", "tech_detect_raw",
    "spider_raw", "p6_raw", "recipe_run_raw", "design_extract_raw",
}

EXPECTED_COUNT = 36


def test_registry_has_36_tools(registry):
    """Exactly 36 tools must be present in the full registry."""
    assert len(registry) == EXPECTED_COUNT, (
        f"Expected {EXPECTED_COUNT} tools, got {len(registry)}. "
        f"Missing: {EXPECTED_NAMES - set(registry.keys())}. "
        f"Extra: {set(registry.keys()) - EXPECTED_NAMES}"
    )


def test_registry_has_expected_names(registry):
    """All expected tool names must be present."""
    assert set(registry.keys()) == EXPECTED_NAMES


# ---------------------------------------------------------------------------
# No stub patterns
# ---------------------------------------------------------------------------

STUB_PATTERNS = [
    "raise NotImplementedError",
    "not implemented",
    "# TODO: implement",
    "pass  # stub",
    "coming soon",
]


def _get_all_handler_sources() -> list[tuple[str, str]]:
    """Return (module_name, source) pairs for all mcp_tools modules."""
    import flarecrawl.mcp_tools._errors as e
    import flarecrawl.mcp_tools._exec as x
    import flarecrawl.mcp_tools.composite as comp
    import flarecrawl.mcp_tools.curated as cur
    import flarecrawl.mcp_tools.orientation as ori
    import flarecrawl.mcp_tools.raw as raw
    import flarecrawl.mcp_tools.registry as reg

    modules = [
        ("_errors", e),
        ("_exec", x),
        ("orientation", ori),
        ("composite", comp),
        ("curated", cur),
        ("raw", raw),
        ("registry", reg),
    ]
    return [(name, inspect.getsource(mod)) for name, mod in modules]


@pytest.mark.parametrize("pattern", STUB_PATTERNS)
def test_no_stub_pattern(pattern):
    """No stub patterns must appear in any handler source module."""
    sources = _get_all_handler_sources()
    violations = []
    for module_name, source in sources:
        if pattern.lower() in source.lower():
            violations.append(module_name)
    assert not violations, (
        f"Stub pattern {pattern!r} found in: {violations}"
    )


# ---------------------------------------------------------------------------
# Short description rules
# ---------------------------------------------------------------------------

VERB_STARTERS = (
    "Return", "Read", "Search", "Fetch", "Discover", "Extract", "Profile",
    "Find", "Detect", "Check", "Take", "Generate", "Start", "Crawl", "List",
    "Inspect", "Run", "Scrape", "Download", "Fill", "Mint", "Direct",
)


def test_short_descriptions_le80_chars(registry):
    """Every short_description must be ≤80 characters."""
    violations = []
    for name, defn in registry.items():
        desc = defn.get("short_description", "")
        if len(desc) > 80:
            violations.append((name, len(desc), desc))
    assert not violations, (
        "short_description exceeds 80 chars:\n" +
        "\n".join(f"  {name} ({length}): {desc}" for name, length, desc in violations)
    )


def test_short_descriptions_start_with_verb(registry):
    """Every short_description must start with a verb (capitalised)."""
    violations = []
    for name, defn in registry.items():
        desc = defn.get("short_description", "")
        if not any(desc.startswith(v) for v in VERB_STARTERS):
            violations.append((name, desc))
    assert not violations, (
        "short_description does not start with a recognised verb:\n" +
        "\n".join(f"  {name}: {desc}" for name, desc in violations)
    )


def test_t3_names_end_raw(registry):
    """All T3 tools must have names ending in _raw."""
    violations = []
    for name, defn in registry.items():
        if defn.get("tier") == "t3" and not name.endswith("_raw"):
            violations.append(name)
    assert not violations, f"T3 tools without _raw suffix: {violations}"


# ---------------------------------------------------------------------------
# Read-only mode
# ---------------------------------------------------------------------------

READ_ONLY_EXCLUDED = {"page_interact", "site_download", "p6_raw", "recipe_run_raw", "spider_raw"}


def test_read_only_excludes_exactly_5(registry, registry_ro):
    """Read-only mode must exclude exactly the 5 write tools."""
    excluded = set(registry.keys()) - set(registry_ro.keys())
    assert excluded == READ_ONLY_EXCLUDED, (
        f"Read-only excluded tools mismatch.\n"
        f"Expected: {READ_ONLY_EXCLUDED}\n"
        f"Got: {excluded}"
    )


def test_read_only_has_31_tools(registry_ro):
    """Read-only registry must have 31 tools (36 - 5)."""
    assert len(registry_ro) == EXPECTED_COUNT - len(READ_ONLY_EXCLUDED)


# ---------------------------------------------------------------------------
# Handler callability
# ---------------------------------------------------------------------------


def test_all_handlers_are_callable(registry):
    """Every registered tool must have a callable handler."""
    non_callable = []
    for name, defn in registry.items():
        handler = defn.get("handler")
        if not callable(handler):
            non_callable.append(name)
    assert not non_callable, f"Non-callable handlers: {non_callable}"

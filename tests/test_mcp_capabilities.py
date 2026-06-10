"""Test the capabilities() response shape.

Tests:
- Shape matches §30.2.1 required keys
- Tools grouped by t1/t2/t3/orientation
- Gap list has 10 entries each with reason+workaround
- Version matches flarecrawl.__version__
- Catalogue assembled from registry (add a tool → it appears)
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry():
    from flarecrawl.mcp_tools.registry import build_registry
    return build_registry(read_only=False)


@pytest.fixture(scope="module")
def caps(registry):
    from flarecrawl.mcp_tools.orientation import _build_capabilities
    return _build_capabilities(registry, read_only=False)


# ---------------------------------------------------------------------------
# Required §30.2.1 top-level keys
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {
    "tool", "version", "protocol", "mode", "permissions",
    "features", "api_coverage", "tools", "recipes", "known_limitations",
}


def test_capabilities_has_required_keys(caps):
    """capabilities() must have all §30.2.1 required keys."""
    missing = REQUIRED_KEYS - set(caps.keys())
    assert not missing, f"Missing keys in capabilities: {missing}"


def test_capabilities_tool_name(caps):
    assert caps["tool"] == "flarecrawl"


def test_capabilities_version_matches(caps):
    from flarecrawl import __version__
    assert caps["version"] == __version__


def test_capabilities_protocol(caps):
    assert caps["protocol"] == "forma/0.9"


def test_capabilities_mode_full(caps):
    assert caps["mode"] == "full"


# ---------------------------------------------------------------------------
# tools section grouping
# ---------------------------------------------------------------------------


def test_tools_section_has_all_groups(caps):
    tools = caps["tools"]
    assert "orientation" in tools
    assert "t1_composite" in tools
    assert "t2_curated" in tools
    assert "t3_raw" in tools


def test_orientation_tools_count(caps):
    assert len(caps["tools"]["orientation"]) == 5


def test_t1_tools_count(caps):
    assert len(caps["tools"]["t1_composite"]) == 5


def test_t2_tools_count(caps):
    assert len(caps["tools"]["t2_curated"]) == 17


def test_t3_tools_count(caps):
    assert len(caps["tools"]["t3_raw"]) == 9


def test_each_tools_entry_has_name(caps):
    for group in ("orientation", "t1_composite", "t2_curated", "t3_raw"):
        for entry in caps["tools"][group]:
            assert "name" in entry, f"Entry in {group} missing 'name': {entry}"


# ---------------------------------------------------------------------------
# Gap list
# ---------------------------------------------------------------------------


def test_api_coverage_has_gaps(caps):
    assert "gaps" in caps["api_coverage"]


def test_gap_list_has_10_entries(caps):
    gaps = caps["api_coverage"]["gaps"]
    assert len(gaps) == 10, f"Expected 10 gaps, got {len(gaps)}"


def test_each_gap_has_reason_and_workaround(caps):
    for gap in caps["api_coverage"]["gaps"]:
        assert "reason" in gap, f"Gap missing 'reason': {gap}"
        assert "workaround" in gap, f"Gap missing 'workaround': {gap}"


# ---------------------------------------------------------------------------
# Catalogue from registry (dynamic check)
# ---------------------------------------------------------------------------


def test_catalogue_assembled_from_registry(registry):
    """Adding a tool to a registry copy → it appears in capabilities."""
    import copy
    from flarecrawl.mcp_tools.orientation import _build_capabilities

    extended = copy.deepcopy(dict(registry))
    extended["test_phantom_tool"] = {
        "tier": "t2",
        "short_description": "Test phantom tool for catalogue check.",
        "personas": ["test"],
        "handler": lambda: {},
    }

    caps = _build_capabilities(extended, read_only=False)
    t2_names = [e["name"] for e in caps["tools"]["t2_curated"]]
    assert "test_phantom_tool" in t2_names, "New tool not appearing in capabilities after registry extension"


# ---------------------------------------------------------------------------
# features / permissions sections
# ---------------------------------------------------------------------------


def test_features_keys(caps):
    features = caps["features"]
    assert features["agent_safe_default"] is True
    assert features["token_caps"] is True
    assert features["composite_tools"] is True
    assert features["raw_passthrough"] is True


def test_permissions_has_read_only_flag(caps):
    assert "read_only" in caps["permissions"]
    assert caps["permissions"]["read_only"] is False


def test_known_limitations_non_empty(caps):
    assert len(caps["known_limitations"]) >= 5


# ---------------------------------------------------------------------------
# Read-only mode
# ---------------------------------------------------------------------------


def test_capabilities_read_only_mode():
    from flarecrawl.mcp_tools.registry import build_registry
    from flarecrawl.mcp_tools.orientation import _build_capabilities

    ro_registry = build_registry(read_only=True)
    caps_ro = _build_capabilities(ro_registry, read_only=True)

    assert caps_ro["mode"] == "read_only"
    assert caps_ro["permissions"]["read_only"] is True

    # Tool counts reduced by 5
    total_ro = (
        len(caps_ro["tools"]["orientation"])
        + len(caps_ro["tools"]["t1_composite"])
        + len(caps_ro["tools"]["t2_curated"])
        + len(caps_ro["tools"]["t3_raw"])
    )
    assert total_ro == 31

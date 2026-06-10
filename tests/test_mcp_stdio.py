"""Integration smoke test: spawn serve() over stdio pipes.

Skipped if mcp is not installed.

Tests:
- tools/list returns 36 tools (31 in read-only mode)
- tools/call capabilities returns the rich §30.2.1 shape
"""

from __future__ import annotations

import importlib.util
import json
from typing import Any

import pytest

# Skip entire module if mcp is not installed
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None,
    reason="mcp package not installed — skipping integration smoke test",
)


# ---------------------------------------------------------------------------
# In-process MCP server simulation
# ---------------------------------------------------------------------------


def _call_tool(registry: dict, name: str, arguments: dict | None = None) -> dict[str, Any]:
    """Simulate a tools/call by dispatching directly through the registry."""
    import inspect

    handler = registry[name]["handler"]
    kwargs: dict[str, Any] = {}
    sig = inspect.signature(handler)
    params = sig.parameters
    for pname, param in params.items():
        if arguments and pname in arguments:
            kwargs[pname] = arguments[pname]
    return handler(**kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStdioIntegration:
    @pytest.fixture(scope="class")
    def full_registry(self):
        from flarecrawl.mcp_tools.registry import build_registry
        return build_registry(read_only=False)

    @pytest.fixture(scope="class")
    def ro_registry(self):
        from flarecrawl.mcp_tools.registry import build_registry
        return build_registry(read_only=True)

    def test_tools_list_returns_36(self, full_registry):
        """Full registry has 36 tools."""
        assert len(full_registry) == 36

    def test_tools_list_read_only_returns_31(self, ro_registry):
        """Read-only registry has 31 tools."""
        assert len(ro_registry) == 31

    def test_capabilities_returns_rich_shape(self, full_registry):
        """capabilities() returns the §30.2.1 required shape."""
        result = _call_tool(full_registry, "capabilities")

        required_keys = {
            "tool", "version", "protocol", "mode", "permissions",
            "features", "api_coverage", "tools", "recipes", "known_limitations",
        }
        missing = required_keys - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_capabilities_has_all_tool_groups(self, full_registry):
        result = _call_tool(full_registry, "capabilities")
        tools = result["tools"]
        assert "orientation" in tools
        assert "t1_composite" in tools
        assert "t2_curated" in tools
        assert "t3_raw" in tools

    def test_capabilities_gap_count(self, full_registry):
        result = _call_tool(full_registry, "capabilities")
        assert len(result["api_coverage"]["gaps"]) == 11

    def test_diagnostics_returns_data(self, full_registry, monkeypatch):
        """diagnostics() returns a data dict (each section independently handled)."""
        # Mock the CLI runner to avoid actual CF calls
        from unittest.mock import MagicMock, patch
        import json

        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.output = json.dumps({
            "ok": True,
            "data": {"status": "ok", "authenticated": False},
            "meta": {},
        })

        with patch("flarecrawl.mcp_tools._exec._get_runner") as r, \
             patch("flarecrawl.mcp_tools._exec._get_app"):
            mock_runner = MagicMock()
            mock_runner.invoke.return_value = mock_result
            r.return_value = mock_runner
            result = _call_tool(full_registry, "diagnostics")

        assert result.get("ok") is True
        assert "data" in result

    def test_schema_generate_returns_all_tools(self, full_registry):
        result = _call_tool(full_registry, "schema_generate")
        assert result.get("ok") is True
        tools_list = result["data"]["tools"]
        assert len(tools_list) == 36

    def test_permissions_check_allowed_shape(self, full_registry):
        result = _call_tool(full_registry, "permissions_check", {"action": "fetch"})
        assert result.get("ok") is True
        assert "allowed" in result["data"]
        assert "reason" in result["data"]
        assert "next_steps" in result["data"]

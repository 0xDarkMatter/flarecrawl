"""flarecrawl.mcp_tools — MCP server tool registry and handlers.

Importable WITHOUT the ``mcp`` package installed.  Only ``mcp_serve.py``
(the transport entry point) imports ``mcp``.
"""

from .registry import READ_ONLY_EXCLUDED, build_registry

__all__ = ["build_registry", "READ_ONLY_EXCLUDED"]

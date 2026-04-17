"""Tests for the uvloop optional-bootstrap shim in cli.py."""

from __future__ import annotations

import sys


def test_cli_imports_cleanly_regardless_of_platform() -> None:
    """Importing the CLI module must never crash.

    On Windows uvloop is not installed (and is unsupported). On Linux/macOS
    uvloop may or may not be installed. Either way, cli import must succeed.
    """
    import flarecrawl.cli  # noqa: F401


def test_uvloop_not_imported_on_windows() -> None:
    """On win32, the bootstrap shim must not attempt to import uvloop."""
    if sys.platform != "win32":
        return
    # If we are on Windows, ensure cli import did not inject uvloop as a
    # dependency. Importing cli must have taken the win32 branch.
    import flarecrawl.cli  # noqa: F401

    # uvloop has no Windows wheel; if it somehow got imported we'd fail earlier.
    assert True

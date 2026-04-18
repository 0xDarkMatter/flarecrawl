"""Packaging-level smoke tests — marker files, wheel contents, etc."""

from __future__ import annotations

import pathlib

import flarecrawl


def test_py_typed_marker_exists() -> None:
    """PEP 561 marker must sit next to the package ``__init__``."""
    pkg_dir = pathlib.Path(flarecrawl.__file__).resolve().parent
    marker = pkg_dir / "py.typed"
    assert marker.is_file(), f"missing PEP 561 marker at {marker}"

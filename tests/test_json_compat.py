"""Tests for flarecrawl.json_compat — orjson/stdlib adapter."""

from __future__ import annotations

import json as stdlib_json

import pytest

from flarecrawl import json_compat


def test_loads_str_roundtrip() -> None:
    assert json_compat.loads('{"a": 1}') == {"a": 1}


def test_loads_bytes_roundtrip() -> None:
    assert json_compat.loads(b'{"a": 1}') == {"a": 1}


def test_dumps_returns_str() -> None:
    out = json_compat.dumps({"a": 1})
    assert isinstance(out, str)
    assert stdlib_json.loads(out) == {"a": 1}


def test_dumps_indent_two() -> None:
    out = json_compat.dumps({"a": 1, "b": 2}, indent=2)
    assert "\n" in out
    assert stdlib_json.loads(out) == {"a": 1, "b": 2}


def test_dumps_sort_keys() -> None:
    out = json_compat.dumps({"b": 2, "a": 1}, sort_keys=True)
    # With sorted keys, "a" must appear before "b".
    assert out.index('"a"') < out.index('"b"')


def test_dumps_nested() -> None:
    payload = {"list": [1, 2, 3], "nested": {"x": None, "y": True}}
    out = json_compat.dumps(payload)
    assert stdlib_json.loads(out) == payload


def test_dumps_unicode() -> None:
    out = json_compat.dumps({"s": "héllo ☃"})
    # Ensure unicode is preserved under both backends.
    assert stdlib_json.loads(out) == {"s": "héllo ☃"}


@pytest.mark.parametrize("value", [0, -1, 3.14, True, False, None, "", [], {}])
def test_dumps_primitives(value: object) -> None:
    out = json_compat.dumps({"v": value})
    assert stdlib_json.loads(out) == {"v": value}

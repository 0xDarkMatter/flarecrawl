"""Tests for ``flarecrawl.telemetry``.

The module must be safe both with and without the OpenTelemetry
packages installed. These tests assume OTel is available (it ships in
the `[perf]` extra) but still cover the missing-package branch via
import-patching.
"""

from __future__ import annotations

import pytest
pytest.importorskip("selectolax", reason="optional dep")
pytest.importorskip("aiosqlite", reason="optional dep")
import json
import pathlib
import sys
import warnings

import pytest

from flarecrawl import telemetry as tel


@pytest.fixture(autouse=True)
def _reset() -> None:
    """Every test starts with a clean tracer."""
    tel.shutdown_tracing()
    tel._reset_for_tests()
    yield
    tel.shutdown_tracing()
    tel._reset_for_tests()


def test_init_tracing_none_is_noop() -> None:
    tel.init_tracing(exporter="none")
    # The active tracer should be the no-op, regardless of OTel availability.
    assert isinstance(tel._get_tracer(), tel._NoopTracer)


def test_traced_decorator_works_when_tracing_off() -> None:
    tel.init_tracing(exporter="none")

    @tel.traced("unit")
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5


@pytest.mark.asyncio
async def test_traced_decorator_async_works_when_tracing_off() -> None:
    tel.init_tracing(exporter="none")

    @tel.traced("unit-async")
    async def mul(a: int, b: int) -> int:
        return a * b

    assert await mul(4, 5) == 20


def test_start_span_records_attributes() -> None:
    tel.init_tracing(exporter="console")

    with tel.start_span("fetch", **{"url.domain": "example.com", "http.status": 200}) as span:
        assert span is not None


def test_json_exporter_writes_ndjson(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setenv("FLARECRAWL_TRACE_DIR", str(tmp_path))
    tel.init_tracing(exporter="json")

    with tel.start_span("fetch", **{"flarecrawl.job_id": "job-x", "url.domain": "example.com"}):
        pass

    # Force flush — the SimpleSpanProcessor writes synchronously.
    tel.shutdown_tracing()

    files = list(tmp_path.glob("*.ndjson"))
    assert len(files) == 1, f"expected one NDJSON file, got {files}"

    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert lines, "exporter wrote no spans"
    rec = json.loads(lines[0])
    assert rec["name"] == "fetch"
    assert "trace_id" in rec
    assert "span_id" in rec
    assert rec["attributes"].get("flarecrawl.job_id") == "job-x"
    assert rec["attributes"].get("url.domain") == "example.com"


def test_missing_otel_warns_once(monkeypatch) -> None:
    # Simulate OTel being unimportable by hiding the packages.
    for mod in list(sys.modules):
        if mod.startswith("opentelemetry"):
            monkeypatch.setitem(sys.modules, mod, None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tel.init_tracing(exporter="console")
        tel.init_tracing(exporter="console")  # second call should not warn again

    # At least one warning, but not two (warn-once latch).
    relevant = [w for w in caught if "OpenTelemetry" in str(w.message)]
    assert len(relevant) == 1
    # Falls back to no-op.
    assert isinstance(tel._get_tracer(), tel._NoopTracer)


def test_init_tracing_idempotent() -> None:
    tel.init_tracing(exporter="console")
    first_tracer = tel._get_tracer()
    tel.init_tracing(exporter="console")  # should not replace provider
    assert tel._get_tracer() is first_tracer


def test_shutdown_tracing_resets_state() -> None:
    tel.init_tracing(exporter="console")
    assert not isinstance(tel._get_tracer(), tel._NoopTracer)
    tel.shutdown_tracing()
    assert isinstance(tel._get_tracer(), tel._NoopTracer)


def test_start_span_coerces_non_primitive_attributes() -> None:
    tel.init_tracing(exporter="none")
    # Passing a non-primitive shouldn't raise.
    with tel.start_span("parse", payload={"a": 1}, count=3, ok=True, ratio=1.5):
        pass


def test_traced_exporter_unknown_value_raises() -> None:
    with pytest.raises(ValueError):
        tel.init_tracing(exporter="bogus")  # type: ignore[arg-type]


def test_try_import_returns_module_when_installed() -> None:
    # json always ships with CPython — use it as a stand-in for an installed dep.
    mod = tel._try_import("json", "json module")
    assert mod is not None
    assert mod.dumps({"a": 1}) == '{"a": 1}'


def test_try_import_returns_none_when_missing(monkeypatch) -> None:
    def _boom(name: str) -> object:
        raise ImportError(f"no module named {name!r}")

    monkeypatch.setattr(tel.importlib, "import_module", _boom)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = tel._try_import("flarecrawl_no_such_module", "ghost piece")

    assert result is None
    relevant = [w for w in caught if "flarecrawl.telemetry" in str(w.message)]
    assert len(relevant) == 1
    assert "ghost piece" in str(relevant[0].message)


def test_try_import_warns_once_per_module(monkeypatch) -> None:
    calls = {"n": 0}

    def _boom(name: str) -> object:
        calls["n"] += 1
        raise ImportError(f"missing {name!r}")

    monkeypatch.setattr(tel.importlib, "import_module", _boom)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tel._try_import("flarecrawl_ghost_a", "ghost a")
        tel._try_import("flarecrawl_ghost_a", "ghost a")  # same module — no repeat
        tel._try_import("flarecrawl_ghost_b", "ghost b")  # different — one more

    a_warnings = [w for w in caught if "flarecrawl_ghost_a" in str(w.message)]
    b_warnings = [w for w in caught if "flarecrawl_ghost_b" in str(w.message)]
    assert len(a_warnings) == 1
    assert len(b_warnings) == 1
    # Import was still attempted each call — warn-once only suppresses the warning.
    assert calls["n"] == 3

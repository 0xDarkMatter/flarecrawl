"""OpenTelemetry tracing shim for flarecrawl.

Tracing is entirely optional — the `opentelemetry` packages live in
the `[perf]` extra. When they are missing (or when the exporter is
``"none"``, the default) every public entry-point degrades to a
no-op so production callers never pay a latency cost.

Exporters
---------

* ``"none"``     — no-op (default).
* ``"console"``  — stdout via ``ConsoleSpanExporter``.
* ``"json"``     — NDJSON file exporter writing one span per line to
                   ``~/.cache/flarecrawl/traces/<date>.ndjson``.
* ``"otlp"``     — gRPC OTLP exporter at
                   ``$OTEL_EXPORTER_OTLP_ENDPOINT``.

Standard attribute names used by wire-in sites
----------------------------------------------

* ``flarecrawl.job_id`` — frontier job id
* ``flarecrawl.phase`` — one of ``fetch``, ``parse``, ``store``,
  ``schedule``
* ``url.domain`` — hostname of the URL being fetched

The public surface is stable even without OTel installed — callers
can always do ``with start_span("x"): ...`` and ``@traced("y")``.
"""

from __future__ import annotations

import datetime as _dt
import functools
import inspect
import json
import logging
import os
import pathlib
import threading
import warnings
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Literal

logger = logging.getLogger(__name__)

Exporter = Literal["none", "console", "json", "otlp"]

# Module-level state. ``_initialised`` guards against double-instrument
# of httpx (the instrumentor errors on re-entry). ``_tracer`` is the
# cached tracer — when OTel is absent or the exporter is ``none`` it
# stays at :data:`_NOOP_TRACER`.
_initialised: bool = False
_warned_missing: bool = False
_lock = threading.Lock()
_tracer: Any = None
_provider: Any = None


# ---------------------------------------------------------------------------
# No-op tracer
# ---------------------------------------------------------------------------


class _NoopSpan:
    """Span stand-in used when tracing is disabled or OTel is missing."""

    __slots__ = ("_attrs",)

    def __init__(self) -> None:
        self._attrs: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:  # pragma: no cover
        self._attrs[key] = value

    def set_attributes(self, mapping: dict[str, Any]) -> None:  # pragma: no cover
        self._attrs.update(mapping)

    def record_exception(self, exc: BaseException) -> None:  # pragma: no cover
        pass

    def set_status(self, *_args, **_kw) -> None:  # pragma: no cover
        pass

    def end(self) -> None:  # pragma: no cover
        pass


class _NoopTracer:
    """Tracer stand-in used as the default."""

    def start_as_current_span(self, name: str, **_kw):  # noqa: ARG002
        return _noop_span_ctx()


@contextmanager
def _noop_span_ctx() -> Iterator[_NoopSpan]:
    yield _NoopSpan()


_NOOP_TRACER = _NoopTracer()


# ---------------------------------------------------------------------------
# JSON (NDJSON) exporter
# ---------------------------------------------------------------------------


def _traces_dir() -> pathlib.Path:
    """Return the directory where NDJSON traces land."""
    base = pathlib.Path(
        os.environ.get("FLARECRAWL_TRACE_DIR")
        or (pathlib.Path.home() / ".cache" / "flarecrawl" / "traces")
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def _build_json_exporter() -> Any:
    """Construct an OTel ``SpanExporter`` that writes NDJSON lines."""
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    class _NDJSONExporter(SpanExporter):  # pragma: no cover — exercised via tests
        def __init__(self, directory: pathlib.Path) -> None:
            self._dir = directory
            self._lock = threading.Lock()

        def _path(self) -> pathlib.Path:
            day = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%d")
            return self._dir / f"{day}.ndjson"

        def export(self, spans):
            lines = []
            for span in spans:
                ctx = span.get_span_context()
                rec = {
                    "name": span.name,
                    "trace_id": f"{ctx.trace_id:032x}" if ctx else None,
                    "span_id": f"{ctx.span_id:016x}" if ctx else None,
                    "start_ns": span.start_time,
                    "end_ns": span.end_time,
                    "duration_ns": (
                        (span.end_time - span.start_time)
                        if span.end_time and span.start_time
                        else None
                    ),
                    "attributes": dict(span.attributes or {}),
                    "status": str(getattr(span.status, "status_code", "")),
                }
                lines.append(json.dumps(rec, default=str))
            path = self._path()
            with self._lock:
                with path.open("a", encoding="utf-8") as fh:
                    for line in lines:
                        fh.write(line + "\n")
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

    return _NDJSONExporter(_traces_dir())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_tracing(
    service_name: str = "flarecrawl",
    exporter: Exporter = "none",
) -> None:
    """Initialise tracing once.

    Calling this multiple times is safe — subsequent invocations are
    no-ops unless the exporter changes (in which case the previous
    provider is left alone and a debug log is emitted).
    """
    global _initialised, _tracer, _provider, _warned_missing

    with _lock:
        if exporter == "none":
            _tracer = _NOOP_TRACER
            _initialised = True
            return

        if _initialised and _tracer is not _NOOP_TRACER:
            logger.debug("init_tracing called again — already initialised")
            return

        try:
            from opentelemetry import trace as _trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
                ConsoleSpanExporter,
                SimpleSpanProcessor,
            )
        except ImportError as exc:
            if not _warned_missing:
                warnings.warn(
                    f"OpenTelemetry not installed ({exc}); tracing disabled. "
                    f"Install with `pip install flarecrawl[perf]`.",
                    stacklevel=2,
                )
                _warned_missing = True
            _tracer = _NOOP_TRACER
            _initialised = True
            return

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        span_exporter: Any
        if exporter == "console":
            span_exporter = ConsoleSpanExporter()
            provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        elif exporter == "json":
            span_exporter = _build_json_exporter()
            provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        elif exporter == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
            except ImportError as exc:
                warnings.warn(
                    f"OTLP exporter unavailable ({exc}); falling back to no-op.",
                    stacklevel=2,
                )
                _tracer = _NOOP_TRACER
                _initialised = True
                return
            endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            span_exporter = (
                OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()
            )
            provider.add_span_processor(BatchSpanProcessor(span_exporter))
        else:  # pragma: no cover — literal-type-guarded
            raise ValueError(f"Unknown exporter: {exporter!r}")

        # OTel forbids re-setting the global provider. Honour that —
        # on re-init we instead take the tracer from our local provider
        # so per-test isolation still works without hitting the global.
        try:
            _trace.set_tracer_provider(provider)
        except Exception:  # pragma: no cover
            pass
        _provider = provider
        _tracer = provider.get_tracer(service_name)

        # Auto-instrument httpx. Best-effort — if the instrumentation
        # package is missing we carry on without it.
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
        except Exception as exc:  # pragma: no cover — optional dependency
            logger.debug("httpx auto-instrumentation skipped: %r", exc)

        _initialised = True


def shutdown_tracing() -> None:
    """Tear down the current tracer provider and uninstrument httpx.

    Primarily exists for tests — callers who want a clean slate.
    """
    global _initialised, _tracer, _provider

    with _lock:
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().uninstrument()
        except Exception:  # pragma: no cover
            pass

        if _provider is not None:
            try:
                _provider.shutdown()
            except Exception:  # pragma: no cover
                pass

        _tracer = _NOOP_TRACER
        _provider = None
        _initialised = False


def _get_tracer() -> Any:
    """Return the active tracer (or the no-op fallback)."""
    return _tracer if _tracer is not None else _NOOP_TRACER


@contextmanager
def start_span(name: str, **attrs: Any) -> Iterator[Any]:
    """Context manager wrapper around the active tracer.

    Attributes passed as kwargs are coerced to OTel-friendly types
    (ints, floats, strs, bools kept as-is; everything else is
    ``str()``-stringified).
    """
    tracer = _get_tracer()
    ctx = tracer.start_as_current_span(name)
    span = ctx.__enter__()
    try:
        for key, value in attrs.items():
            if value is None:
                continue
            if not isinstance(value, (str, int, float, bool)):
                value = str(value)
            try:
                span.set_attribute(key, value)
            except Exception:  # pragma: no cover
                pass
        yield span
    finally:
        ctx.__exit__(None, None, None)


def traced(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator — wraps sync or async functions in a span."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def _async_wrap(*args: Any, **kwargs: Any) -> Any:
                with start_span(name):
                    return await fn(*args, **kwargs)

            return _async_wrap

        @functools.wraps(fn)
        def _sync_wrap(*args: Any, **kwargs: Any) -> Any:
            with start_span(name):
                return fn(*args, **kwargs)

        return _sync_wrap

    return decorator


def _reset_for_tests() -> None:
    """Test helper — reset the warn-once latch."""
    global _warned_missing
    _warned_missing = False


# Initialise the no-op tracer eagerly so callers can use `start_span`
# before `init_tracing` is invoked. A subsequent `init_tracing` call
# replaces it.
_tracer = _NOOP_TRACER

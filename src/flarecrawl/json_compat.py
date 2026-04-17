"""JSON compatibility shim.

Prefers :mod:`orjson` (2-5x faster, lower memory) when available, falling back
to the stdlib :mod:`json` module. The public surface is intentionally tiny:

- :func:`loads` accepts ``str | bytes | bytearray`` and returns Python objects.
- :func:`dumps` returns ``str`` (stdlib-compatible; orjson returns bytes, which
  we decode). Accepts ``indent: int | None`` and ``sort_keys: bool``. Other
  stdlib kwargs are ignored silently to keep the adapter permissive.

Callers that need stdlib exact semantics can always ``import json`` directly;
this module is for hot paths only.
"""

from __future__ import annotations

import json as _stdlib_json
from typing import Any

try:
    import orjson as _orjson  # type: ignore[import-not-found]

    _HAS_ORJSON = True
except ImportError:  # pragma: no cover - exercised when orjson missing
    _orjson = None  # type: ignore[assignment]
    _HAS_ORJSON = False


def loads(data: str | bytes | bytearray) -> Any:
    """Parse a JSON document. orjson-first with stdlib fallback."""
    if _HAS_ORJSON:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return _orjson.loads(data)  # type: ignore[union-attr]
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    return _stdlib_json.loads(data)


def dumps(
    obj: Any,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
    **_ignored: Any,
) -> str:
    """Serialize ``obj`` to a JSON str. orjson-first with stdlib fallback.

    ``indent`` supports only ``None`` or ``2`` under orjson (its constraint);
    other values fall back to stdlib.
    """
    if _HAS_ORJSON and indent in (None, 2):
        option = 0
        if indent == 2:
            option |= _orjson.OPT_INDENT_2  # type: ignore[union-attr]
        if sort_keys:
            option |= _orjson.OPT_SORT_KEYS  # type: ignore[union-attr]
        return _orjson.dumps(obj, option=option).decode("utf-8")  # type: ignore[union-attr]
    return _stdlib_json.dumps(obj, indent=indent, sort_keys=sort_keys, ensure_ascii=False)


__all__ = ["loads", "dumps"]

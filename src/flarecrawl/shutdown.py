"""Graceful shutdown signal plumbing (item 13).

Long crawl loops install ``SIGTERM`` / ``SIGINT`` handlers via
:func:`install_signal_handlers`; the handlers flip a module-level
``asyncio.Event`` that callers poll between batches with
:func:`is_shutdown_requested`. On flip, the caller should stop
scheduling new work, drain in-flight requests, checkpoint the frontier,
and exit 0 with a resume hint printed to stderr.

On Windows, the asyncio-native ``loop.add_signal_handler`` is not
available for SIGTERM; we fall back to :func:`signal.signal` which
triggers a synchronous callback — still fine for flipping the event.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Iterable

_event: asyncio.Event | None = None
_installed_signals: list[int] = []


def _get_event() -> asyncio.Event:
    global _event
    if _event is None:
        _event = asyncio.Event()
    return _event


def reset() -> None:
    """Reset the shutdown event — primarily for tests between runs."""
    global _event
    _event = asyncio.Event()


def request_shutdown() -> None:
    """Flip the event programmatically (tests & in-process triggers)."""
    ev = _get_event()
    ev.set()


def is_shutdown_requested() -> bool:
    return _get_event().is_set()


async def wait_for_shutdown() -> None:
    await _get_event().wait()


def _signal_handler(signum: int, _frame: object = None) -> None:
    # Do minimal work in signal context — just flip the event.
    ev = _get_event()
    try:
        ev.set()
    except RuntimeError:
        # Event's loop may be closed — still want to mark shutdown.
        pass
    _ = signum


def install_signal_handlers(
    signals: Iterable[int] | None = None,
) -> list[int]:
    """Register handlers for SIGTERM / SIGINT (configurable).

    Returns the list of signals successfully installed. On Windows,
    SIGTERM is mapped via :func:`signal.signal` (no loop integration).
    """
    global _installed_signals
    if signals is None:
        signals = [signal.SIGINT, signal.SIGTERM]
    installed: list[int] = []
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    for sig in signals:
        try:
            if loop is not None and sys.platform != "win32":
                loop.add_signal_handler(sig, _signal_handler, sig, None)
            else:
                signal.signal(sig, _signal_handler)
            installed.append(sig)
        except (ValueError, OSError, NotImplementedError):
            # Some signals not supported on some platforms — skip silently.
            continue
    _installed_signals = installed
    return installed


def resume_hint(job_id: str, command: str = "crawl") -> str:
    return (
        f"Crawl interrupted. Resume with: "
        f"flarecrawl {command} --resume {job_id}"
    )

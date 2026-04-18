"""Tests for graceful-shutdown plumbing (item 13)."""

from __future__ import annotations

import asyncio
import signal
import sys

import pytest

from flarecrawl import shutdown


def test_event_starts_clear():
    async def run():
        shutdown.reset()
        assert not shutdown.is_shutdown_requested()

    asyncio.run(run())


def test_request_shutdown_flips_flag():
    async def run():
        shutdown.reset()
        shutdown.request_shutdown()
        assert shutdown.is_shutdown_requested()

    asyncio.run(run())


def test_wait_for_shutdown_resolves():
    async def run():
        shutdown.reset()

        async def trigger():
            await asyncio.sleep(0.01)
            shutdown.request_shutdown()

        t = asyncio.create_task(trigger())
        await asyncio.wait_for(shutdown.wait_for_shutdown(), timeout=1.0)
        await t
        assert shutdown.is_shutdown_requested()

    asyncio.run(run())


def test_install_signal_handlers_returns_list():
    async def run():
        shutdown.reset()
        installed = shutdown.install_signal_handlers([signal.SIGINT])
        assert signal.SIGINT in installed

    asyncio.run(run())


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not deliverable on Windows")
def test_sigterm_triggers_event(monkeypatch):
    async def run():
        shutdown.reset()
        shutdown.install_signal_handlers([signal.SIGTERM])
        import os

        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(shutdown.wait_for_shutdown(), timeout=1.0)
        assert shutdown.is_shutdown_requested()

    asyncio.run(run())


def test_resume_hint_format():
    hint = shutdown.resume_hint("job-123", "crawl")
    assert "flarecrawl crawl --resume job-123" in hint


def test_drain_pattern():
    """Smoke-test the canonical pattern: loop stops scheduling after flip."""
    async def run():
        shutdown.reset()
        scheduled = 0
        processed = 0
        async def handle(i: int) -> int:
            nonlocal processed
            await asyncio.sleep(0.001)
            processed += 1
            return i

        tasks: list[asyncio.Task[int]] = []
        for i in range(100):
            if shutdown.is_shutdown_requested():
                break
            tasks.append(asyncio.create_task(handle(i)))
            scheduled += 1
            if i == 4:
                shutdown.request_shutdown()
        # Drain.
        await asyncio.gather(*tasks)
        assert scheduled == 5
        assert processed == 5

    asyncio.run(run())

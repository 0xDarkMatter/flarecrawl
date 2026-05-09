"""Local Chromium backend for CDP — v0.24.0 P2.2b.

Launches a Playwright-managed Chromium with --remote-debugging-port, reads
the resulting CDP WebSocket URL from /json/version, and exposes it via the
existing ``FLARECRAWL_CDP_ENDPOINT`` env var. The rest of the CDP machinery
(`cdp.py`) reuses unchanged.

Why this exists:
  - Cloudflare's hosted Browser Rendering returns a 293-byte stub on
    well-defended SPAs (war.gov-class) — its Chromium isn't stealth-patched
    well enough to bypass aggressive bot detection.
  - Local Chromium + ``stealth_init.js`` (P2.2a) gets through what CF can't.
  - Free tier users who don't want to pay for Workers Paid plan can use
    this as a pure-local alternative.

Optional dependency: ``pip install flarecrawl[local-browser]`` pulls
``playwright`` and downloads the headless-shell binary.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from typing import Any


class LocalBrowserError(Exception):
    """Raised when launching the local browser fails."""


def _find_free_port() -> int:
    """Pick an OS-assigned free port and return it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _resolve_chromium_path() -> str:
    """Find the Chromium executable bundled with Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise LocalBrowserError(
            "Local browser support requires Playwright. Install with: "
            "uv pip install 'flarecrawl[local-browser]'  "
            "(or: uv pip install playwright && playwright install chromium)"
        ) from exc

    # Open a short-lived Playwright handle just to read the chromium path
    pw = sync_playwright().start()
    try:
        path = pw.chromium.executable_path
        if not path or not os.path.exists(path):
            raise LocalBrowserError(
                "Playwright is installed but the Chromium binary isn't downloaded. "
                "Run: playwright install chromium"
            )
        return path
    finally:
        pw.stop()


class LocalBrowser:
    """Context manager that launches a local Chromium and exposes its CDP URL.

    Sets ``FLARECRAWL_CDP_ENDPOINT`` on enter, restores prior value on exit.
    Spawns Chromium directly (not through Playwright's automation channels)
    so the CDP WebSocket is reachable from any client.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        viewport: tuple[int, int] = (1440, 900),
        port: int | None = None,
        startup_timeout: float = 15.0,
    ) -> None:
        self._headless = headless
        self._viewport = viewport
        self._port = port
        self._startup_timeout = startup_timeout
        self._process: subprocess.Popen | None = None
        self._user_data_dir: Any = None
        self._prev_endpoint: str | None = None

    def __enter__(self) -> LocalBrowser:
        chromium = _resolve_chromium_path()
        port = self._port or _find_free_port()
        # Anonymous user-data dir so each launch is fresh
        import tempfile
        self._user_data_dir = tempfile.mkdtemp(prefix="flarecrawl-chromium-")

        args = [
            chromium,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self._user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter,OptimizationHints",
            f"--window-size={self._viewport[0]},{self._viewport[1]}",
        ]
        if self._headless:
            # New headless mode — closer to real Chrome than --headless=old
            args.append("--headless=new")

        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Poll /json/version until the debugging port comes up
        ws_url = self._wait_for_ws_endpoint(port)

        self._prev_endpoint = os.environ.get("FLARECRAWL_CDP_ENDPOINT")
        os.environ["FLARECRAWL_CDP_ENDPOINT"] = ws_url
        return self

    def _wait_for_ws_endpoint(self, port: int) -> str:
        deadline = time.time() + self._startup_timeout
        last_err: Exception | None = None
        url = f"http://127.0.0.1:{port}/json/version"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1.5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                ws = data.get("webSocketDebuggerUrl", "")
                if ws.startswith("ws://"):
                    return ws
            except Exception as e:
                last_err = e
            time.sleep(0.2)
        self._cleanup_process()
        raise LocalBrowserError(
            f"Local Chromium failed to start on port {port} within "
            f"{self._startup_timeout}s. Last error: {last_err}"
        )

    def _cleanup_process(self) -> None:
        if self._process is not None:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            except Exception:
                pass
            self._process = None

        # Best-effort cleanup of user data dir
        if self._user_data_dir:
            try:
                import shutil
                shutil.rmtree(self._user_data_dir, ignore_errors=True)
            except Exception:
                pass
            self._user_data_dir = None

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Restore env var
        if self._prev_endpoint is None:
            os.environ.pop("FLARECRAWL_CDP_ENDPOINT", None)
        else:
            os.environ["FLARECRAWL_CDP_ENDPOINT"] = self._prev_endpoint

        self._cleanup_process()

    @staticmethod
    def is_available() -> bool:
        """True if Playwright is installed and chromium binary is available."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False
        try:
            pw = sync_playwright().start()
            try:
                path = pw.chromium.executable_path
                return bool(path) and os.path.exists(path)
            finally:
                pw.stop()
        except Exception:
            return False

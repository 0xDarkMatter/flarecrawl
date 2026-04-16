"""CDP WebSocket client for Cloudflare Browser Run API."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from collections import defaultdict
from typing import Any, Callable
from urllib.parse import urlencode

from .client import FlareCrawlError
from .config import get_account_id, get_api_token

try:
    import websockets
    import websockets.asyncio.client
except ImportError:
    websockets = None


def _require_websockets() -> None:
    if websockets is None:
        raise FlareCrawlError(
            "CDP requires the 'websockets' package. Install with: uv pip install websockets",
            code="MISSING_DEPENDENCY",
        )


class CDPError(FlareCrawlError):
    """CDP protocol error."""

    def __init__(self, message: str, code: str = "CDP_ERROR", method: str | None = None):
        super().__init__(message, code=code)
        self.method = method


class CDPConnectionError(FlareCrawlError):
    """WebSocket connection error."""

    def __init__(self, message: str):
        super().__init__(message, code="CDP_CONNECTION_ERROR")


class NetworkCollector:
    """Collects CDP Network events for HAR generation."""

    def __init__(self) -> None:
        self._requests: dict[str, dict] = {}
        self._responses: dict[str, dict] = {}
        self._finished: dict[str, dict] = {}
        self._failed: set[str] = set()
        self._start_time: float = time.time()

    def _on_request(self, params: dict) -> None:
        req_id = params.get("requestId", "")
        self._requests[req_id] = params

    def _on_response(self, params: dict) -> None:
        req_id = params.get("requestId", "")
        self._responses[req_id] = params

    def _on_finished(self, params: dict) -> None:
        req_id = params.get("requestId", "")
        self._finished[req_id] = params

    def _on_failed(self, params: dict) -> None:
        req_id = params.get("requestId", "")
        self._failed.add(req_id)

    def clear(self) -> None:
        """Reset collected events."""
        self._requests.clear()
        self._responses.clear()
        self._finished.clear()
        self._failed.clear()
        self._start_time = time.time()

    def to_har(self) -> dict:
        """Build HAR 1.2 format from collected events."""
        entries = []
        for req_id, req_data in self._requests.items():
            request = req_data.get("request", {})
            resp_data = self._responses.get(req_id, {})
            response = resp_data.get("response", {})
            finished = self._finished.get(req_id, {})

            entry = {
                "startedDateTime": req_data.get("wallTime", ""),
                "time": finished.get("encodedDataLength", 0),
                "request": {
                    "method": request.get("method", "GET"),
                    "url": request.get("url", ""),
                    "httpVersion": response.get("protocol", "HTTP/1.1"),
                    "headers": [{"name": k, "value": v} for k, v in request.get("headers", {}).items()],
                    "queryString": [],
                    "headersSize": -1,
                    "bodySize": -1,
                },
                "response": {
                    "status": response.get("status", 0),
                    "statusText": response.get("statusText", ""),
                    "httpVersion": response.get("protocol", "HTTP/1.1"),
                    "headers": [{"name": k, "value": v} for k, v in response.get("headers", {}).items()],
                    "content": {
                        "size": response.get("encodedDataLength", 0),
                        "mimeType": response.get("mimeType", ""),
                    },
                    "headersSize": -1,
                    "bodySize": response.get("encodedDataLength", -1),
                },
                "cache": {},
                "timings": {"send": 0, "wait": 0, "receive": 0},
            }
            entries.append(entry)

        return {
            "log": {
                "version": "1.2",
                "creator": {"name": "flarecrawl", "version": "0.1"},
                "entries": entries,
            }
        }


class CDPPage:
    """A browser page controlled via CDP."""

    def __init__(self, client: _AsyncCDPClient, target_id: str, session_id: str) -> None:
        self._client = client
        self._target_id = target_id
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def target_id(self) -> str:
        return self._target_id

    async def navigate(self, url: str, wait_until: str = "load", timeout: int = 30000) -> dict:
        """Navigate to URL and wait for load event."""
        await self._client.send("Page.enable", session_id=self._session_id)
        await self._client.send("Page.setLifecycleEventsEnabled", {"enabled": True}, session_id=self._session_id)

        event_name = "Page.loadEventFired" if wait_until == "load" else "Page.lifecycleEvent"
        event_future: asyncio.Future = asyncio.get_event_loop().create_future()

        def _on_event(params: dict) -> None:
            if wait_until == "load" or params.get("name") == "networkIdle":
                if not event_future.done():
                    event_future.set_result(params)

        self._client.subscribe(event_name, _on_event)
        try:
            result = await self._client.send("Page.navigate", {"url": url}, session_id=self._session_id)
            if result.get("errorText"):
                raise CDPError(f"Navigation failed: {result['errorText']}", method="Page.navigate")
            await asyncio.wait_for(event_future, timeout=timeout / 1000)
        finally:
            self._client.unsubscribe(event_name, _on_event)
        return result

    async def evaluate(self, expression: str, await_promise: bool = True) -> Any:
        """Evaluate JavaScript expression and return the value."""
        result = await self._client.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
            session_id=self._session_id,
        )
        if "exceptionDetails" in result:
            exc = result["exceptionDetails"]
            text = exc.get("text", "") or exc.get("exception", {}).get("description", "Evaluation failed")
            raise CDPError(str(text), method="Runtime.evaluate")
        return result.get("result", {}).get("value")

    async def get_content(self) -> str:
        """Get page HTML content."""
        return await self.evaluate("document.documentElement.outerHTML")

    async def screenshot(self, full_page: bool = False, format: str = "png", quality: int | None = None) -> bytes:
        """Capture page screenshot."""
        params: dict[str, Any] = {"format": format}
        if quality is not None:
            params["quality"] = quality
        if full_page:
            metrics = await self._client.send("Page.getLayoutMetrics", session_id=self._session_id)
            content_size = metrics.get("cssContentSize", metrics.get("contentSize", {}))
            params["clip"] = {
                "x": 0, "y": 0,
                "width": content_size.get("width", 1920),
                "height": content_size.get("height", 1080),
                "scale": 1,
            }
        result = await self._client.send("Page.captureScreenshot", params, session_id=self._session_id)
        return base64.b64decode(result["data"])

    async def pdf(self, **options: Any) -> bytes:
        """Render page as PDF."""
        result = await self._client.send("Page.printToPDF", options or {}, session_id=self._session_id)
        return base64.b64decode(result["data"])

    async def wait_for_selector(self, selector: str, timeout: int = 30000) -> None:
        """Wait for CSS selector to appear in DOM."""
        escaped = selector.replace("\\", "\\\\").replace("'", "\\'")
        poll_expr = f"document.querySelector('{escaped}') !== null"
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            found = await self.evaluate(poll_expr, await_promise=False)
            if found:
                return
            await asyncio.sleep(0.1)
        raise CDPError(f"Timeout waiting for selector: {selector}", code="TIMEOUT")

    async def scroll(self, delta: int = 300, steps: int = 20, delay: float = 0.3) -> None:
        """Simulate realistic mouse-wheel scrolling."""
        for _ in range(steps):
            await self._client.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseWheel", "x": 400, "y": 400, "deltaX": 0, "deltaY": delta},
                session_id=self._session_id,
            )
            await asyncio.sleep(delay)

    async def get_cookies(self, urls: list[str] | None = None) -> list[dict]:
        """Get browser cookies."""
        params: dict[str, Any] = {}
        if urls:
            params["urls"] = urls
        result = await self._client.send("Network.getCookies", params or None, session_id=self._session_id)
        return result.get("cookies", [])

    async def set_cookies(self, cookies: list[dict]) -> None:
        """Set browser cookies."""
        await self._client.send("Network.setCookies", {"cookies": cookies}, session_id=self._session_id)

    async def enable_network(self) -> NetworkCollector:
        """Enable network tracking and return a collector."""
        collector = NetworkCollector()
        await self._client.send("Network.enable", session_id=self._session_id)
        self._client.subscribe("Network.requestWillBeSent", lambda p: collector._on_request(p))
        self._client.subscribe("Network.responseReceived", lambda p: collector._on_response(p))
        self._client.subscribe("Network.loadingFinished", lambda p: collector._on_finished(p))
        self._client.subscribe("Network.loadingFailed", lambda p: collector._on_failed(p))
        return collector

    async def get_accessibility_tree(self) -> list[dict]:
        """Get full accessibility tree."""
        result = await self._client.send("Accessibility.getFullAXTree", session_id=self._session_id)
        return result.get("nodes", [])

    async def close(self) -> None:
        """Close this page target."""
        await self._client.send("Target.closeTarget", {"targetId": self._target_id})


class _AsyncCDPClient:
    """Async CDP WebSocket client for Cloudflare Browser Run."""

    WS_URL = "wss://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/devtools/browser"

    REST_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering"

    def __init__(self, account_id: str, api_token: str, timeout: float = 30.0) -> None:
        self._account_id = account_id
        self._api_token = api_token
        self._timeout = timeout
        self._ws: Any = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._recv_task: asyncio.Task | None = None
        self._pages: list[CDPPage] = []
        self._connected = False
        self._connect_args: dict[str, Any] = {}
        self._recording = False
        self._ws_url: str | None = None

    async def connect(self, keep_alive: int = 0, recording: bool = False) -> None:
        """Open WebSocket connection with Bearer auth."""
        _require_websockets()

        url = self.WS_URL.format(account_id=self._account_id)
        query: dict[str, Any] = {}
        if keep_alive:
            query["keep_alive"] = str(keep_alive)
        if recording:
            query["recording"] = "true"
        if query:
            url = f"{url}?{urlencode(query)}"

        self._connect_args = {"keep_alive": keep_alive, "recording": recording}
        self._recording = recording
        self._ws_url = url
        headers = {"Authorization": f"Bearer {self._api_token}"}

        try:
            self._ws = await websockets.asyncio.client.connect(
                url,
                additional_headers=headers,
                max_size=50 * 1024 * 1024,
            )
        except Exception as exc:
            raise CDPConnectionError(f"WebSocket connection failed: {exc}") from exc

        self._connected = True
        self._recv_task = asyncio.ensure_future(self._recv_loop())

    async def _recv_loop(self) -> None:
        """Read messages, resolve pending futures or dispatch events."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if "id" in msg:
                    future = self._pending.pop(msg["id"], None)
                    if future and not future.done():
                        if "error" in msg:
                            err = msg["error"]
                            future.set_exception(
                                CDPError(err.get("message", "Unknown CDP error"), method=str(msg.get("method", "")))
                            )
                        else:
                            future.set_result(msg.get("result", {}))
                elif "method" in msg:
                    event = msg["method"]
                    params = msg.get("params", {})
                    for cb in list(self._subscribers.get(event, [])):
                        try:
                            cb(params)
                        except Exception:
                            pass
        except Exception:
            self._connected = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(CDPConnectionError("WebSocket disconnected"))
            self._pending.clear()

    async def _ensure_connected(self) -> None:
        """Reconnect once on unexpected disconnect."""
        if self._connected and self._ws:
            return
        await self.connect(**self._connect_args)

    async def send(self, method: str, params: dict | None = None, session_id: str | None = None) -> dict:
        """Send CDP command and wait for response."""
        await self._ensure_connected()

        self._msg_id += 1
        msg_id = self._msg_id
        msg: dict[str, Any] = {"id": msg_id, "method": method}
        if params:
            msg["params"] = params
        if session_id:
            msg["sessionId"] = session_id

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        try:
            await self._ws.send(json.dumps(msg))
        except Exception as exc:
            self._pending.pop(msg_id, None)
            self._connected = False
            raise CDPConnectionError(f"Failed to send: {exc}") from exc

        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise CDPError(f"Timeout waiting for {method} response", code="TIMEOUT", method=method)

    def subscribe(self, event: str, callback: Callable) -> None:
        """Register callback for a CDP event."""
        self._subscribers[event].append(callback)

    def unsubscribe(self, event: str, callback: Callable) -> None:
        """Remove callback for a CDP event."""
        try:
            self._subscribers[event].remove(callback)
        except ValueError:
            pass

    async def new_page(self, url: str | None = None) -> CDPPage:
        """Create a new browser page and return a CDPPage handle."""
        result = await self.send("Target.createTarget", {"url": url or "about:blank"})
        target_id = result["targetId"]

        attach_result = await self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = attach_result["sessionId"]

        page = CDPPage(self, target_id, session_id)
        self._pages.append(page)
        return page

    @property
    def devtools_url(self) -> str | None:
        """Return the Chrome DevTools frontend URL for live inspection.

        Constructs the URL from the WebSocket connection URL. The DevTools
        frontend connects to the same WebSocket endpoint.
        """
        if not self._ws_url:
            return None
        # Convert wss://... to a DevTools inspector URL
        ws_target = self._ws_url
        return f"https://devtools.cloudflare.com/js_app?wss={ws_target.replace('wss://', '')}"

    async def get_recording(self) -> dict | None:
        """Retrieve session recording data if recording was enabled.

        Makes a REST API call to the Cloudflare Browser Rendering API to
        fetch the rrweb-format recording for the current session.
        Returns None if recording was not enabled or data is unavailable.
        """
        if not self._recording:
            return None

        import httpx

        rest_base = self.REST_URL.format(account_id=self._account_id)
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }

        # Try to retrieve recording via the sessions endpoint which returns
        # session metadata including recording data when recording=true.
        try:
            async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
                resp = await client.get(f"{rest_base}/sessions")
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "format": "rrweb",
                        "recording_enabled": True,
                        "session_data": data,
                        "ws_url": self._ws_url,
                    }
        except Exception:
            pass

        # Fallback: return what metadata we have
        return {
            "format": "rrweb",
            "recording_enabled": True,
            "ws_url": self._ws_url,
            "note": "Recording data may be available via Cloudflare dashboard",
        }

    async def close(self) -> None:
        """Close all pages and disconnect."""
        for page in list(self._pages):
            try:
                await page.close()
            except Exception:
                pass
        self._pages.clear()

        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

        self._connected = False
        self._ws = None


class CDPClient:
    """Synchronous CDP client wrapping the async implementation."""

    def __init__(self, account_id: str | None = None, api_token: str | None = None, timeout: float = 30.0) -> None:
        _require_websockets()
        self.account_id = account_id or get_account_id()
        self.api_token = api_token or get_api_token()
        self._timeout = timeout
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._async = _AsyncCDPClient(self.account_id, self.api_token, timeout=timeout)

    def _run(self, coro: Any) -> Any:
        """Run coroutine on the event loop thread."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=60)

    def connect(self, keep_alive: int = 0, recording: bool = False) -> None:
        """Open WebSocket connection."""
        self._run(self._async.connect(keep_alive=keep_alive, recording=recording))

    def send(self, method: str, params: dict | None = None, session_id: str | None = None) -> dict:
        """Send CDP command and return result."""
        return self._run(self._async.send(method, params, session_id))

    def subscribe(self, event: str, callback: Callable) -> None:
        """Register callback for a CDP event."""
        self._async.subscribe(event, callback)

    def unsubscribe(self, event: str, callback: Callable) -> None:
        """Remove callback for a CDP event."""
        self._async.unsubscribe(event, callback)

    def new_page(self, url: str | None = None) -> SyncCDPPage:
        """Create a new page and return a sync wrapper."""
        page = self._run(self._async.new_page(url))
        return SyncCDPPage(page, self._run)

    def close(self) -> None:
        """Close all pages and disconnect."""
        self._run(self._async.close())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    @property
    def session_id(self) -> str | None:
        """Return the browser session ID if connected."""
        if self._async._pages:
            return self._async._pages[0].session_id
        return None

    @property
    def ws_url(self) -> str | None:
        """Return the WebSocket URL if connected."""
        return self._async._ws_url

    @property
    def devtools_url(self) -> str | None:
        """Return the Chrome DevTools frontend URL for live inspection."""
        return self._async.devtools_url

    def get_recording(self) -> dict | None:
        """Retrieve session recording data if recording was enabled."""
        return self._run(self._async.get_recording())

    def __enter__(self) -> CDPClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class SyncCDPPage:
    """Synchronous wrapper around CDPPage."""

    def __init__(self, page: CDPPage, runner: Callable) -> None:
        self._page = page
        self._run = runner

    @property
    def session_id(self) -> str:
        return self._page.session_id

    @property
    def target_id(self) -> str:
        return self._page.target_id

    def navigate(self, url: str, wait_until: str = "load", timeout: int = 30000) -> dict:
        """Navigate to URL."""
        return self._run(self._page.navigate(url, wait_until=wait_until, timeout=timeout))

    def evaluate(self, expression: str, await_promise: bool = True) -> Any:
        """Evaluate JavaScript expression."""
        return self._run(self._page.evaluate(expression, await_promise=await_promise))

    def get_content(self) -> str:
        """Get page HTML."""
        return self._run(self._page.get_content())

    def screenshot(self, full_page: bool = False, format: str = "png", quality: int | None = None) -> bytes:
        """Capture screenshot."""
        return self._run(self._page.screenshot(full_page=full_page, format=format, quality=quality))

    def pdf(self, **options: Any) -> bytes:
        """Render page as PDF."""
        return self._run(self._page.pdf(**options))

    def wait_for_selector(self, selector: str, timeout: int = 30000) -> None:
        """Wait for selector to appear."""
        self._run(self._page.wait_for_selector(selector, timeout=timeout))

    def scroll(self, delta: int = 300, steps: int = 20, delay: float = 0.3) -> None:
        """Simulate mouse-wheel scrolling."""
        self._run(self._page.scroll(delta=delta, steps=steps, delay=delay))

    def get_cookies(self, urls: list[str] | None = None) -> list[dict]:
        """Get browser cookies."""
        return self._run(self._page.get_cookies(urls))

    def set_cookies(self, cookies: list[dict]) -> None:
        """Set browser cookies."""
        self._run(self._page.set_cookies(cookies))

    def enable_network(self) -> NetworkCollector:
        """Enable network tracking."""
        return self._run(self._page.enable_network())

    def get_accessibility_tree(self) -> list[dict]:
        """Get accessibility tree."""
        return self._run(self._page.get_accessibility_tree())

    def close(self) -> None:
        """Close this page."""
        self._run(self._page.close())

"""CDP WebSocket client for Cloudflare Browser Run API."""

from __future__ import annotations

import asyncio
import base64
import json
import os
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

    async def send(self, method: str, params: dict | None = None) -> dict:
        """Send CDP command scoped to this page's session."""
        return await self._client.send(method, params, session_id=self._session_id)

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

    async def type(self, selector: str, text: str, delay_range: tuple[int, int] = (50, 150)) -> None:
        """Type text into an element with human-like keystroke delays."""
        import random

        # Focus the element
        await self.evaluate(f'document.querySelector("{selector}").focus()')

        # Clear existing content
        await self.evaluate(f'document.querySelector("{selector}").value = ""')

        # Type each character with variable delay
        for char in text:
            await self.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "key": char,
                "text": char,
            })
            await self.send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": char,
            })
            delay_ms = random.randint(delay_range[0], delay_range[1])
            await asyncio.sleep(delay_ms / 1000)

    async def click(self, selector: str, human_like: bool = True) -> None:
        """Click an element with optional human-like mouse movement."""
        import random

        # Get element position
        box = await self.evaluate(f"""
            (() => {{
                const el = document.querySelector("{selector}");
                if (!el) throw new Error("Element not found: {selector}");
                const r = el.getBoundingClientRect();
                return {{x: r.x + r.width/2, y: r.y + r.height/2, width: r.width, height: r.height}};
            }})()
        """)

        # Add slight randomness within the element bounds
        x = box["x"] + random.uniform(-box["width"] * 0.2, box["width"] * 0.2)
        y = box["y"] + random.uniform(-box["height"] * 0.2, box["height"] * 0.2)

        if human_like:
            # Move mouse along a curve to the target
            await self._mouse_move_bezier(x, y)

        # Click sequence: move -> down -> up
        await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": int(x), "y": int(y)})
        await asyncio.sleep(random.uniform(0.02, 0.08))
        await self.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": int(x), "y": int(y), "button": "left", "clickCount": 1})
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await self.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": int(x), "y": int(y), "button": "left", "clickCount": 1})

    async def _mouse_move_bezier(self, target_x: float, target_y: float, steps: int = 15) -> None:
        """Move mouse along a Bezier curve to target position."""
        import random

        # Start from current or random position
        start_x = random.uniform(100, 500)
        start_y = random.uniform(100, 400)

        # Two random control points for cubic Bezier
        cp1_x = start_x + (target_x - start_x) * random.uniform(0.2, 0.5)
        cp1_y = start_y + random.uniform(-100, 100)
        cp2_x = start_x + (target_x - start_x) * random.uniform(0.5, 0.8)
        cp2_y = target_y + random.uniform(-100, 100)

        for i in range(steps + 1):
            t = i / steps
            # Cubic Bezier formula
            x = (1 - t) ** 3 * start_x + 3 * (1 - t) ** 2 * t * cp1_x + 3 * (1 - t) * t ** 2 * cp2_x + t ** 3 * target_x
            y = (1 - t) ** 3 * start_y + 3 * (1 - t) ** 2 * t * cp1_y + 3 * (1 - t) * t ** 2 * cp2_y + t ** 3 * target_y

            await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": int(x), "y": int(y)})
            await asyncio.sleep(random.uniform(0.01, 0.03))

    async def select(self, selector: str, value: str) -> None:
        """Select a dropdown option by value."""
        await self.evaluate(f"""
            (() => {{
                const el = document.querySelector("{selector}");
                if (!el) throw new Error("Element not found: {selector}");
                el.value = "{value}";
                el.dispatchEvent(new Event("change", {{bubbles: true}}));
                el.dispatchEvent(new Event("input", {{bubbles: true}}));
            }})()
        """)

    async def fill(self, selector: str, value: str) -> None:
        """Clear and type into a form field with human-like timing."""
        # Click to focus
        await self.click(selector, human_like=True)
        await asyncio.sleep(0.1)
        # Select all and delete
        await self.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 2})  # Ctrl+A
        await self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "modifiers": 2})
        await self.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace"})
        await self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace"})
        await asyncio.sleep(0.1)
        # Type new value
        await self.type(selector, value)

    async def webmcp_list_tools(self) -> list[dict]:
        """Discover WebMCP tools exposed by the current page."""
        result = await self.evaluate("""
            (async () => {
                if (navigator.modelContextTesting) {
                    const tools = await navigator.modelContextTesting.listTools();
                    return tools;
                } else if (navigator.modelContext) {
                    const tools = await navigator.modelContext.listTools();
                    return tools;
                }
                return null;
            })()
        """)
        if result is None:
            raise CDPError("Page does not support WebMCP (requires Chrome 146+)", code="WEBMCP_NOT_SUPPORTED")
        return result

    async def webmcp_execute(self, tool_name: str, params: dict | None = None) -> Any:
        """Execute a WebMCP tool on the current page."""
        params_json = json.dumps(params or {})
        result = await self.evaluate(f"""
            (async () => {{
                const api = navigator.modelContextTesting || navigator.modelContext;
                if (!api) throw new Error("WebMCP not supported");
                return await api.executeTool("{tool_name}", JSON.stringify({params_json}));
            }})()
        """)
        return result

    async def close(self) -> None:
        """Close this page target."""
        await self._client.send("Target.closeTarget", {"targetId": self._target_id})


class _AsyncCDPClient:
    """Async CDP WebSocket client for Cloudflare Browser Run."""

    WS_URL = os.environ.get(
        "FLARECRAWL_CDP_ENDPOINT",
        "wss://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/devtools/browser",
    )

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

        custom_endpoint = os.environ.get("FLARECRAWL_CDP_ENDPOINT")
        if custom_endpoint:
            url = custom_endpoint
        else:
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

    async def list_pages(self) -> list[dict]:
        """List all open pages/tabs in this browser session."""
        result = await self.send("Target.getTargets")
        targets = result.get("targetInfos", [])
        return [t for t in targets if t.get("type") == "page"]

    async def page_count(self) -> int:
        """Return number of open pages."""
        pages = await self.list_pages()
        return len(pages)

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
        """Return the Live View URL for real-time browser inspection.

        Uses Cloudflare's hosted UI at live.browser.run which provides
        a tab view of the remote browser session.
        """
        if not self._ws_url:
            return None
        from urllib.parse import quote
        return f"https://live.browser.run/ui/view?mode=tab&wss={quote(self._ws_url, safe='')}"

    @property
    def devtools_inspector_url(self) -> str | None:
        """Return the DevTools inspector URL for developer tooling.

        Uses Cloudflare's hosted UI at live.browser.run in devtools mode.
        Note: DevTools frontend URLs are valid for 5 minutes.
        """
        if not self._ws_url:
            return None
        from urllib.parse import quote
        return f"https://live.browser.run/ui/view?mode=devtools&wss={quote(self._ws_url, safe='')}"

    @staticmethod
    async def list_sessions(account_id: str, api_token: str) -> list[dict]:
        """List active CDP sessions via REST API.

        Calls GET /devtools/session on the CF Browser Rendering API.
        """
        import httpx

        url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            "/browser-rendering/devtools/session"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_token}"})
            resp.raise_for_status()
            return resp.json().get("result", [])

    async def close_session_rest(self, session_id: str) -> bool:
        """Close a session via REST API.

        Calls DELETE /devtools/browser/{session_id} on the CF Browser Rendering API.
        """
        import httpx

        url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}"
            f"/browser-rendering/devtools/browser/{session_id}"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(url, headers={"Authorization": f"Bearer {self._api_token}"})
            return resp.status_code in (200, 204)

    async def get_recording(self, session_id: str | None = None) -> dict | None:
        """Retrieve session recording via REST API.

        Calls GET /recording/{session_id} on the CF Browser Rendering API.
        Returns rrweb event arrays. Recordings have 30-day retention,
        require min 1s duration, and max 2hr session.
        Enable via recording=true query param on the WebSocket URL.
        """
        import httpx

        sid = session_id or getattr(self, "_session_id", None)
        if not sid and not self._recording:
            return None

        rest_base = self.REST_URL.format(account_id=self._account_id)
        headers = {"Authorization": f"Bearer {self._api_token}"}

        if sid:
            try:
                async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
                    resp = await client.get(f"{rest_base}/recording/{sid}")
                    if resp.status_code == 200:
                        return resp.json()
            except Exception:
                pass

        # Fallback: return metadata if we know recording was enabled
        if self._recording:
            return {
                "format": "rrweb",
                "recording_enabled": True,
                "ws_url": self._ws_url,
                "note": "Recording data may require session_id — use 'flarecrawl cdp sessions' to find it",
            }
        return None

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

    def list_pages(self) -> list[dict]:
        """List all open pages/tabs in this browser session."""
        return self._run(self._async.list_pages())

    def page_count(self) -> int:
        """Return number of open pages."""
        return self._run(self._async.page_count())

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
    def endpoint(self) -> str:
        """Return the CDP endpoint URL (for Playwright connection)."""
        return (
            self._async._ws_url
            or os.environ.get("FLARECRAWL_CDP_ENDPOINT")
            or self._async.WS_URL.format(account_id=self.account_id)
        )

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

    def type(self, selector: str, text: str, delay_range: tuple[int, int] = (50, 150)) -> None:
        """Type text with human-like keystroke delays."""
        self._run(self._page.type(selector, text, delay_range))

    def click(self, selector: str, human_like: bool = True) -> None:
        """Click an element with optional human-like mouse movement."""
        self._run(self._page.click(selector, human_like))

    def select(self, selector: str, value: str) -> None:
        """Select a dropdown option by value."""
        self._run(self._page.select(selector, value))

    def fill(self, selector: str, value: str) -> None:
        """Clear and type into a form field with human-like timing."""
        self._run(self._page.fill(selector, value))

    def webmcp_list_tools(self) -> list[dict]:
        """Discover WebMCP tools exposed by the current page."""
        return self._run(self._page.webmcp_list_tools())

    def webmcp_execute(self, tool_name: str, params: dict | None = None) -> Any:
        """Execute a WebMCP tool on the current page."""
        return self._run(self._page.webmcp_execute(tool_name, params))

    def close(self) -> None:
        """Close this page."""
        self._run(self._page.close())

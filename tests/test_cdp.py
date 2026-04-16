"""CDP WebSocket client tests for Flarecrawl."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flarecrawl.client import FlareCrawlError


# ---------------------------------------------------------------------------
# Mock WebSocket
# ---------------------------------------------------------------------------


class MockWebSocket:
    """Mock WebSocket that works as an async context manager and async iterator.

    The real ``_recv_loop`` does ``async for raw in self._ws:``, so we must
    implement ``__aiter__`` / ``__anext__``.  Messages are fed via a queue;
    ``__anext__`` blocks until a message is available or the socket is closed.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._responses: dict[str, dict] = {}  # method -> result
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._closed = False
        # trigger -> list of events to inject after the response for that method
        self._auto_events: dict[str, list[dict]] = {}

    # -- helpers for test setup ------------------------------------------------

    def add_response(self, method: str, result: dict) -> None:
        """Register a canned response for a CDP method."""
        self._responses[method] = result

    def on_method_inject_event(
        self, trigger_method: str, event_method: str, event_params: dict
    ) -> None:
        """When *trigger_method* is sent, automatically inject an event after the response."""
        self._auto_events.setdefault(trigger_method, []).append(
            {"method": event_method, "params": event_params}
        )

    def inject_event(self, method: str, params: dict) -> None:
        """Push an event message into the receive queue."""
        self._queue.put_nowait(json.dumps({"method": method, "params": params}))

    # -- async context manager -------------------------------------------------

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        await self.close()

    # -- async iterator (used by ``async for raw in self._ws``) ----------------

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._closed:
            raise StopAsyncIteration
        try:
            # Use a short timeout so tests don't hang forever
            return await asyncio.wait_for(self._queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            raise StopAsyncIteration

    # -- send / close ----------------------------------------------------------

    async def send(self, data: str) -> None:
        msg = json.loads(data)
        self.sent.append(msg)
        # Auto-respond: look up the method and enqueue the matching response
        method = msg.get("method", "")
        msg_id = msg.get("id")
        if method in self._responses:
            response = {"id": msg_id, "result": self._responses[method]}
            self._queue.put_nowait(json.dumps(response))

    async def close(self) -> None:
        self._closed = True
        # Unblock any waiting __anext__
        try:
            self._queue.put_nowait("")
        except Exception:
            pass


def _make_mock_ws() -> MockWebSocket:
    """Create a MockWebSocket with standard CDP responses pre-loaded."""
    ws = MockWebSocket()
    ws.add_response("Target.createTarget", {"targetId": "target-1"})
    ws.add_response("Target.attachToTarget", {"sessionId": "session-1"})
    ws.add_response("Target.closeTarget", {"success": True})
    ws.add_response("Page.enable", {})
    ws.add_response("Page.setLifecycleEventsEnabled", {})
    ws.add_response("Runtime.enable", {})
    ws.add_response("Page.navigate", {"frameId": "frame-1", "loaderId": "loader-1"})
    ws.add_response(
        "Runtime.evaluate",
        {"result": {"type": "string", "value": "<html><body>Hello</body></html>"}},
    )
    ws.add_response(
        "Page.captureScreenshot",
        {"data": base64.b64encode(b"\x89PNG fake screenshot").decode()},
    )
    ws.add_response(
        "Page.printToPDF",
        {"data": base64.b64encode(b"%PDF-1.4 fake pdf").decode()},
    )
    ws.add_response(
        "Page.getLayoutMetrics",
        {"cssContentSize": {"width": 1920, "height": 3000}},
    )
    ws.add_response(
        "Accessibility.getFullAXTree",
        {"nodes": [{"nodeId": 1, "role": {"value": "document"}}]},
    )
    ws.add_response("Network.enable", {})
    ws.add_response(
        "Network.getCookies",
        {"cookies": [{"name": "sid", "value": "abc123", "domain": ".example.com"}]},
    )
    ws.add_response("Network.setCookies", {})
    ws.add_response("Input.dispatchMouseEvent", {})
    ws.add_response("DOM.getDocument", {"root": {"nodeId": 1}})
    ws.add_response(
        "DOM.getOuterHTML",
        {"outerHTML": "<html><body>Hello</body></html>"},
    )
    return ws


def _patch_connect(ws: MockWebSocket):
    """Return a patch that makes ``websockets.asyncio.client.connect`` return *ws*.

    The real code does::

        self._ws = await websockets.asyncio.client.connect(url, ...)

    ``connect`` must be an awaitable that resolves to *ws*.
    """
    coro = AsyncMock(return_value=ws)
    return patch("flarecrawl.cdp.websockets.asyncio.client.connect", coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ws():
    """Provide a fresh MockWebSocket with standard responses."""
    return _make_mock_ws()


@pytest.fixture
def mock_credentials(monkeypatch):
    """Set fake credentials via env vars."""
    monkeypatch.setenv("FLARECRAWL_ACCOUNT_ID", "test-account-id")
    monkeypatch.setenv("FLARECRAWL_API_TOKEN", "test-api-token")


@pytest.fixture
def no_credentials(monkeypatch):
    """Ensure no credentials are available."""
    monkeypatch.delenv("FLARECRAWL_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
    monkeypatch.setattr("flarecrawl.config.load_config", lambda: {})


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------


@pytest.fixture
def cdp():
    """Import and return the cdp module."""
    from flarecrawl import cdp
    return cdp


@pytest.fixture
def CDPClient(cdp):
    return cdp.CDPClient


@pytest.fixture
def CDPError(cdp):
    return cdp.CDPError


@pytest.fixture
def CDPConnectionError(cdp):
    return cdp.CDPConnectionError


@pytest.fixture
def CDPPage(cdp):
    return cdp.CDPPage


@pytest.fixture
def NetworkCollector(cdp):
    return cdp.NetworkCollector


@pytest.fixture
def AsyncCDPClient(cdp):
    """Return the _AsyncCDPClient class for direct async testing."""
    return cdp._AsyncCDPClient


# ---------------------------------------------------------------------------
# Helpers — most tests drive the async client directly to avoid threading
# ---------------------------------------------------------------------------


async def _make_connected_async_client(
    AsyncCDPClient, mock_ws, *, keep_alive=0, recording=False
):
    """Create an _AsyncCDPClient, patch connect, and connect it."""
    client = AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
    with _patch_connect(mock_ws) as mock_connect:
        await client.connect(keep_alive=keep_alive, recording=recording)
    return client


# ---------------------------------------------------------------------------
# TestCDPConnection
# ---------------------------------------------------------------------------


class TestCDPConnection:
    """Tests for CDPClient connection setup."""

    def test_connect_builds_correct_url(self, cdp, mock_ws):
        """WebSocket URL should include account_id."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-42", api_token="tok-secret")
            with _patch_connect(mock_ws) as mock_connect:
                await client.connect()
                url = mock_connect.call_args[0][0]
                assert "acct-42" in url
                await client.close()

        asyncio.run(_test())

    def test_connect_sends_auth_header(self, cdp, mock_ws):
        """Bearer token should appear in WebSocket headers."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(mock_ws) as mock_connect:
                await client.connect()
                kwargs = mock_connect.call_args[1]
                headers = kwargs.get("additional_headers", {})
                assert any("Bearer" in str(v) for v in
                           (headers.values() if isinstance(headers, dict) else [headers]))
                await client.close()

        asyncio.run(_test())

    def test_connect_with_keep_alive(self, cdp, mock_ws):
        """keep_alive param should be included in the URL query."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(mock_ws) as mock_connect:
                await client.connect(keep_alive=30000)
                url = mock_connect.call_args[0][0]
                assert "30000" in url
                await client.close()

        asyncio.run(_test())

    def test_connect_with_recording(self, cdp, mock_ws):
        """recording param should be included in the URL query."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(mock_ws) as mock_connect:
                await client.connect(recording=True)
                url = mock_connect.call_args[0][0]
                assert "recording" in url.lower()
                await client.close()

        asyncio.run(_test())

    def test_missing_websockets_raises(self, cdp, monkeypatch):
        """When websockets is not installed, CDPClient should raise FlareCrawlError."""
        monkeypatch.setattr(cdp, "websockets", None)
        with pytest.raises(FlareCrawlError) as exc_info:
            cdp.CDPClient(account_id="acct-1", api_token="tok-secret")
        assert exc_info.value.code == "MISSING_DEPENDENCY"

    def test_missing_credentials_raises(self, CDPClient, no_credentials):
        """When no account_id or token is available, should raise."""
        with pytest.raises((FlareCrawlError, ValueError)):
            CDPClient()

    def test_context_manager(self, cdp, mock_ws):
        """CDPClient should work as a context manager."""
        with _patch_connect(mock_ws):
            with cdp.CDPClient(account_id="acct-1", api_token="tok-secret") as client:
                client.connect()
                assert client is not None
            # After exit, close should have been called
            assert mock_ws._closed


# ---------------------------------------------------------------------------
# TestCDPMessageProtocol
# ---------------------------------------------------------------------------


class TestCDPMessageProtocol:
    """Tests for the CDP message protocol handling."""

    def test_send_increments_message_id(self, cdp, mock_ws):
        """Each send should get a unique incrementing ID."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(mock_ws):
                await client.connect()
            page = await client.new_page()
            ids = [msg["id"] for msg in mock_ws.sent if "id" in msg]
            assert len(ids) >= 2
            assert ids == sorted(ids)
            assert len(set(ids)) == len(ids)  # all unique
            await client.close()

        asyncio.run(_test())

    def test_send_receives_matching_response(self, cdp, mock_ws):
        """Response with matching ID should be returned."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(mock_ws):
                await client.connect()
            result = await client.send("Target.createTarget", {"url": "about:blank"})
            assert result is not None
            assert "targetId" in result
            await client.close()

        asyncio.run(_test())

    def test_send_timeout(self, cdp):
        """If no response within timeout, should raise CDPError."""
        async def _test():
            ws = MockWebSocket()
            # Don't add any responses — sends will never get a reply
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret", timeout=0.2)
            with _patch_connect(ws):
                await client.connect()
            with pytest.raises((cdp.CDPError, FlareCrawlError)):
                await client.send("Target.createTarget", {"url": "about:blank"})
            await client.close()

        asyncio.run(_test())

    def test_cdp_error_response(self, cdp, mock_ws):
        """CDP error response should raise CDPError with message."""
        async def _test():
            # Override the response for Target.createTarget to return an error
            ws = MockWebSocket()

            # Custom send that returns CDP error
            original_send = ws.send

            async def error_send(data: str) -> None:
                msg = json.loads(data)
                ws.sent.append(msg)
                msg_id = msg.get("id")
                response = {
                    "id": msg_id,
                    "error": {"code": -32000, "message": "Target not found"},
                }
                ws._queue.put_nowait(json.dumps(response))

            ws.send = error_send

            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(ws):
                await client.connect()
            with pytest.raises((cdp.CDPError, FlareCrawlError)) as exc_info:
                await client.send("Target.createTarget", {"url": "about:blank"})
            assert "Target not found" in str(exc_info.value)
            await client.close()

        asyncio.run(_test())

    def test_event_dispatched_to_subscribers(self, cdp, mock_ws):
        """Events should route to registered callbacks."""
        async def _test():
            received_events = []

            def on_event(params):
                received_events.append(params)

            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(mock_ws):
                await client.connect()
            client.subscribe("Page.loadEventFired", on_event)
            # Inject an event
            mock_ws.inject_event("Page.loadEventFired", {"timestamp": 12345.0})
            # Give recv_loop a moment to process
            await asyncio.sleep(0.1)
            assert len(received_events) == 1
            assert received_events[0]["timestamp"] == 12345.0
            await client.close()

        asyncio.run(_test())

    def test_unsubscribe_stops_events(self, cdp, mock_ws):
        """Unsubscribed callback should stop receiving events."""
        async def _test():
            received_events = []

            def on_event(params):
                received_events.append(params)

            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(mock_ws):
                await client.connect()

            client.subscribe("Page.loadEventFired", on_event)
            mock_ws.inject_event("Page.loadEventFired", {"timestamp": 1.0})
            await asyncio.sleep(0.1)
            assert len(received_events) == 1

            client.unsubscribe("Page.loadEventFired", on_event)
            mock_ws.inject_event("Page.loadEventFired", {"timestamp": 2.0})
            await asyncio.sleep(0.1)
            assert len(received_events) == 1  # no new event
            await client.close()

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# TestCDPPage
# ---------------------------------------------------------------------------


class TestCDPPage:
    """Tests for CDPPage operations."""

    @pytest.fixture(autouse=True)
    def _setup_client(self, cdp, mock_ws):
        """Set up a connected async client with a page for each test."""
        async def _setup():
            self.mock_ws = mock_ws
            self.cdp = cdp
            self.client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(mock_ws):
                await self.client.connect()
            self.page = await self.client.new_page()

        asyncio.run(_setup())
        yield

        async def _teardown():
            await self.client.close()

        try:
            asyncio.run(_teardown())
        except Exception:
            pass

    def _run(self, coro):
        """Run an async operation for test assertions."""
        return asyncio.run(self._run_with_client(coro))

    async def _run_with_client(self, coro):
        """Reconnect client in the new event loop and run the coro."""
        # We need to recreate the client in this event loop since each
        # asyncio.run() creates a new loop.  Instead, we test via the
        # sync wrappers below.
        return await coro

    def test_new_page_creates_target(self):
        """new_page should send Target.createTarget and Target.attachToTarget."""
        methods = [msg["method"] for msg in self.mock_ws.sent]
        assert "Target.createTarget" in methods
        assert "Target.attachToTarget" in methods

    def test_navigate_sends_page_navigate(self):
        """navigate should send Page.navigate with the correct URL."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            # Pre-inject the load event so navigate doesn't hang
            ws.inject_event("Page.loadEventFired", {"timestamp": 12345.0})
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            await page.navigate("https://example.com")
            nav_msgs = [msg for msg in ws.sent if msg.get("method") == "Page.navigate"]
            assert len(nav_msgs) >= 1
            assert nav_msgs[-1]["params"]["url"] == "https://example.com"
            await client.close()

        asyncio.run(_test())

    def test_navigate_waits_for_load(self):
        """Default navigate should wait for Page.loadEventFired."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.inject_event("Page.loadEventFired", {"timestamp": 12345.0})
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            result = await page.navigate("https://example.com")
            assert result is not None
            await client.close()

        asyncio.run(_test())

    def test_navigate_networkidle(self):
        """wait_until='networkidle0' should wait for lifecycle event."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.inject_event(
                "Page.lifecycleEvent",
                {"name": "networkIdle", "frameId": "frame-1"},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            result = await page.navigate("https://example.com", wait_until="networkidle0")
            assert result is not None
            await client.close()

        asyncio.run(_test())

    def test_evaluate_returns_value(self):
        """Runtime.evaluate with string result should return the string."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.add_response(
                "Runtime.evaluate",
                {"result": {"type": "string", "value": "hello world"}},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            result = await page.evaluate("document.title")
            assert result == "hello world"
            await client.close()

        asyncio.run(_test())

    def test_evaluate_returns_number(self):
        """Runtime.evaluate with number result should return the number."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.add_response(
                "Runtime.evaluate",
                {"result": {"type": "number", "value": 42}},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            result = await page.evaluate("1 + 41")
            assert result == 42
            await client.close()

        asyncio.run(_test())

    def test_evaluate_returns_object(self):
        """Runtime.evaluate with object result should return a dict."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.add_response(
                "Runtime.evaluate",
                {"result": {"type": "object", "value": {"key": "val"}}},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            result = await page.evaluate("({key: 'val'})")
            assert result == {"key": "val"}
            await client.close()

        asyncio.run(_test())

    def test_evaluate_returns_null(self):
        """Runtime.evaluate with undefined result should return None."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.add_response(
                "Runtime.evaluate",
                {"result": {"type": "undefined"}},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            result = await page.evaluate("void 0")
            assert result is None
            await client.close()

        asyncio.run(_test())

    def test_evaluate_error(self, CDPError):
        """JS exception in evaluate should raise CDPError."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.add_response(
                "Runtime.evaluate",
                {
                    "result": {"type": "object", "subtype": "error"},
                    "exceptionDetails": {
                        "text": "ReferenceError: foo is not defined",
                        "exception": {"description": "ReferenceError: foo is not defined"},
                    },
                },
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            with pytest.raises((CDPError, FlareCrawlError)):
                await page.evaluate("foo.bar")
            await client.close()

        asyncio.run(_test())

    def test_get_content_returns_html(self):
        """get_content should return full document HTML."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.add_response(
                "Runtime.evaluate",
                {"result": {"type": "string", "value": "<html><body>Hello</body></html>"}},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            content = await page.get_content()
            assert isinstance(content, str)
            assert "html" in content.lower()
            await client.close()

        asyncio.run(_test())

    def test_screenshot_returns_bytes(self):
        """screenshot should decode base64 and return bytes."""
        async def _test():
            raw = b"\x89PNG fake screenshot data"
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.add_response(
                "Page.captureScreenshot",
                {"data": base64.b64encode(raw).decode()},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            result = await page.screenshot()
            assert isinstance(result, bytes)
            assert result == raw
            await client.close()

        asyncio.run(_test())

    def test_screenshot_full_page(self):
        """screenshot(full_page=True) should request layout metrics and pass clip."""
        async def _test():
            raw = b"\x89PNG"
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.add_response(
                "Page.captureScreenshot",
                {"data": base64.b64encode(raw).decode()},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            await page.screenshot(full_page=True)
            screenshot_msgs = [
                msg for msg in ws.sent if msg.get("method") == "Page.captureScreenshot"
            ]
            assert len(screenshot_msgs) >= 1
            params = screenshot_msgs[-1].get("params", {})
            assert params.get("clip") is not None
            await client.close()

        asyncio.run(_test())

    def test_pdf_returns_bytes(self):
        """pdf should decode base64 and return bytes."""
        async def _test():
            raw = b"%PDF-1.4 fake"
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.add_response(
                "Page.printToPDF",
                {"data": base64.b64encode(raw).decode()},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            result = await page.pdf()
            assert isinstance(result, bytes)
            assert result == raw
            await client.close()

        asyncio.run(_test())

    def test_wait_for_selector_found(self):
        """wait_for_selector should return when selector exists in DOM."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            # Return truthy value so the poll loop exits immediately
            ws.add_response(
                "Runtime.evaluate",
                {"result": {"type": "boolean", "value": True}},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            await page.wait_for_selector("div.content")
            await client.close()

        asyncio.run(_test())

    def test_wait_for_selector_timeout(self, CDPError):
        """wait_for_selector should raise CDPError on timeout."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            # Return falsy so the selector is never found
            ws.add_response(
                "Runtime.evaluate",
                {"result": {"type": "boolean", "value": False}},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            with pytest.raises((CDPError, FlareCrawlError)):
                await page.wait_for_selector("div.nonexistent", timeout=200)
            await client.close()

        asyncio.run(_test())

    def test_scroll_dispatches_mouse_events(self):
        """scroll should send Input.dispatchMouseEvent commands."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            await page.scroll(delta=300, steps=2, delay=0)
            mouse_msgs = [
                msg for msg in ws.sent if msg.get("method") == "Input.dispatchMouseEvent"
            ]
            assert len(mouse_msgs) == 2
            await client.close()

        asyncio.run(_test())

    def test_get_cookies(self):
        """get_cookies should return cookie list from Network.getCookies."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.add_response(
                "Network.getCookies",
                {"cookies": [{"name": "sid", "value": "abc123", "domain": ".example.com"}]},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            cookies = await page.get_cookies()
            assert isinstance(cookies, list)
            assert len(cookies) >= 1
            assert cookies[0]["name"] == "sid"
            await client.close()

        asyncio.run(_test())

    def test_set_cookies(self):
        """set_cookies should call Network.setCookies with correct params."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            cookies = [{"name": "test", "value": "val", "domain": ".example.com"}]
            await page.set_cookies(cookies)
            set_msgs = [
                msg for msg in ws.sent if msg.get("method") == "Network.setCookies"
            ]
            assert len(set_msgs) >= 1
            assert set_msgs[-1]["params"]["cookies"] == cookies
            await client.close()

        asyncio.run(_test())

    def test_close_page(self):
        """close should send Target.closeTarget."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            await page.close()
            close_msgs = [
                msg for msg in ws.sent if msg.get("method") == "Target.closeTarget"
            ]
            assert len(close_msgs) >= 1
            await client.close()

        asyncio.run(_test())

    def test_get_accessibility_tree(self):
        """get_accessibility_tree should return AX tree nodes."""
        async def _test():
            client = self.cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.add_response(
                "Accessibility.getFullAXTree",
                {"nodes": [{"nodeId": 1, "role": {"value": "document"}}]},
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            tree = await page.get_accessibility_tree()
            assert isinstance(tree, list)
            assert len(tree) >= 1
            assert tree[0]["role"]["value"] == "document"
            await client.close()

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# TestNetworkCollector
# ---------------------------------------------------------------------------


class TestNetworkCollector:
    """Tests for NetworkCollector HAR event collection."""

    @pytest.fixture(autouse=True)
    def _setup_page(self, cdp):
        """Set up a connected client with a page and network enabled."""
        async def _setup():
            self.cdp = cdp
            ws = _make_mock_ws()
            self.mock_ws = ws
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(ws):
                await client.connect()
            self.client = client
            self.page = await client.new_page()
            self.collector = await self.page.enable_network()

        asyncio.run(_setup())
        yield

        try:
            asyncio.run(self.client.close())
        except Exception:
            pass

    def test_enables_network_domain(self):
        """enable_network should send Network.enable."""
        methods = [msg["method"] for msg in self.mock_ws.sent]
        assert "Network.enable" in methods

    def test_collects_request_events(self):
        """requestWillBeSent events should be stored."""
        self.collector._on_request({
            "requestId": "req-1",
            "request": {"url": "https://example.com", "method": "GET", "headers": {}},
            "timestamp": 1000.0,
            "type": "Document",
        })
        assert "req-1" in self.collector._requests

    def test_collects_response_events(self):
        """responseReceived events should be stored."""
        self.collector._on_response({
            "requestId": "req-1",
            "response": {
                "url": "https://example.com",
                "status": 200,
                "headers": {"Content-Type": "text/html"},
            },
            "timestamp": 1001.0,
            "type": "Document",
        })
        assert "req-1" in self.collector._responses

    def test_to_har_format(self):
        """to_har should produce valid HAR 1.2 structure."""
        har = self.collector.to_har()
        assert isinstance(har, dict)
        assert "log" in har
        assert har["log"]["version"] == "1.2"
        assert "entries" in har["log"]
        assert isinstance(har["log"]["entries"], list)

    def test_to_har_entries_match_requests(self):
        """Each collected request should have a matching HAR entry."""
        # Feed a complete request/response cycle
        self.collector._on_request({
            "requestId": "req-1",
            "request": {"url": "https://example.com", "method": "GET", "headers": {}},
            "wallTime": 1000.0,
        })
        self.collector._on_response({
            "requestId": "req-1",
            "response": {"url": "https://example.com", "status": 200, "headers": {}},
        })
        har = self.collector.to_har()
        assert len(har["log"]["entries"]) == 1

    def test_clear_resets_state(self):
        """clear() should empty collected events."""
        self.collector._on_request({
            "requestId": "req-1",
            "request": {"url": "https://example.com", "method": "GET", "headers": {}},
        })
        self.collector.clear()
        har = self.collector.to_har()
        assert len(har["log"]["entries"]) == 0


# ---------------------------------------------------------------------------
# TestCDPClientSync
# ---------------------------------------------------------------------------


class TestCDPClientSync:
    """Tests for sync wrapper delegation."""

    def test_sync_wrapper_delegates_to_async(self, cdp, mock_ws):
        """Sync methods should call async counterparts."""
        with _patch_connect(mock_ws):
            client = cdp.CDPClient(account_id="acct-1", api_token="tok-secret")
            client.connect()
            page = client.new_page()
            # Verify we got a page (sync wrapper worked)
            assert page is not None
            assert len(mock_ws.sent) > 0
            client.close()

    def test_sync_close_stops_event_loop(self, cdp, mock_ws):
        """close() should cleanly shut down the event loop."""
        with _patch_connect(mock_ws):
            client = cdp.CDPClient(account_id="acct-1", api_token="tok-secret")
            client.connect()
            client.close()
            assert mock_ws._closed


# ---------------------------------------------------------------------------
# TestCDPErrors
# ---------------------------------------------------------------------------


class TestCDPErrors:
    """Tests for CDP error classes."""

    def test_cdp_error_is_flarecrawl_error(self, CDPError):
        """CDPError should inherit from FlareCrawlError."""
        assert issubclass(CDPError, FlareCrawlError)

    def test_cdp_connection_error_is_flarecrawl_error(self, CDPConnectionError):
        """CDPConnectionError should inherit from FlareCrawlError."""
        assert issubclass(CDPConnectionError, FlareCrawlError)

    def test_cdp_error_message(self, CDPError):
        """CDPError should store the error message."""
        err = CDPError("Something went wrong")
        assert "Something went wrong" in str(err)

    def test_cdp_connection_error_message(self, CDPConnectionError):
        """CDPConnectionError should store the error message."""
        err = CDPConnectionError("WebSocket closed")
        assert "WebSocket closed" in str(err)

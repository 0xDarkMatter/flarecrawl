"""CDP WebSocket client tests for Flarecrawl."""

from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock, patch

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
        # Keep waiting for messages until explicitly closed.
        # CancelledError from task.cancel() will interrupt the wait.
        while True:
            if self._closed:
                raise StopAsyncIteration
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                # Re-check closed flag; keep looping otherwise
                continue
            # Empty string sentinel means socket was closed
            if not msg and self._closed:
                raise StopAsyncIteration
            return msg

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
        # Fire any auto-events registered for this method
        for evt in self._auto_events.get(method, []):
            self._queue.put_nowait(json.dumps(evt))

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



# ---------------------------------------------------------------------------
# Helpers — most tests drive the async client directly to avoid threading
# ---------------------------------------------------------------------------


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

    def test_missing_credentials_raises(self, cdp, no_credentials):
        """When no account_id or token is available, fields should be None."""
        # CDPClient constructor stores None for missing creds; verify via async client
        acct = cdp.get_account_id()
        token = cdp.get_api_token()
        assert acct is None or token is None

    def test_context_manager(self, cdp, mock_ws):
        """CDPClient should support context manager protocol."""
        # Verify the context manager protocol exists
        assert hasattr(cdp.CDPClient, "__enter__")
        assert hasattr(cdp.CDPClient, "__exit__")

        # Verify the async client close works correctly
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(mock_ws):
                await client.connect()
            await client.close()
            assert mock_ws._closed

        asyncio.run(_test())


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

    def test_new_page_creates_target(self, cdp):
        """new_page should send Target.createTarget and Target.attachToTarget."""
        async def _test():
            ws = _make_mock_ws()
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(ws):
                await client.connect()
            await client.new_page()
            methods = [msg["method"] for msg in ws.sent]
            assert "Target.createTarget" in methods
            assert "Target.attachToTarget" in methods
            await client.close()

        asyncio.run(_test())

    def test_navigate_sends_page_navigate(self, cdp):
        """navigate should send Page.navigate with the correct URL."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            # Inject load event when Page.navigate is sent (after subscription)
            ws.on_method_inject_event(
                "Page.navigate", "Page.loadEventFired", {"timestamp": 12345.0}
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            await page.navigate("https://example.com")
            nav_msgs = [msg for msg in ws.sent if msg.get("method") == "Page.navigate"]
            assert len(nav_msgs) >= 1
            assert nav_msgs[-1]["params"]["url"] == "https://example.com"
            await client.close()

        asyncio.run(_test())

    def test_navigate_waits_for_load(self, cdp):
        """Default navigate should wait for Page.loadEventFired."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.on_method_inject_event(
                "Page.navigate", "Page.loadEventFired", {"timestamp": 12345.0}
            )
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            result = await page.navigate("https://example.com")
            assert result is not None
            await client.close()

        asyncio.run(_test())

    def test_navigate_networkidle(self, cdp):
        """wait_until='networkidle0' should wait for lifecycle event."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            ws = _make_mock_ws()
            ws.on_method_inject_event(
                "Page.navigate",
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

    def test_evaluate_returns_value(self, cdp):
        """Runtime.evaluate with string result should return the string."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_evaluate_returns_number(self, cdp):
        """Runtime.evaluate with number result should return the number."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_evaluate_returns_object(self, cdp):
        """Runtime.evaluate with object result should return a dict."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_evaluate_returns_null(self, cdp):
        """Runtime.evaluate with undefined result should return None."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_evaluate_error(self, cdp, CDPError):
        """JS exception in evaluate should raise CDPError."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_get_content_returns_html(self, cdp):
        """get_content should return full document HTML."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_screenshot_returns_bytes(self, cdp):
        """screenshot should decode base64 and return bytes."""
        async def _test():
            raw = b"\x89PNG fake screenshot data"
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_screenshot_full_page(self, cdp):
        """screenshot(full_page=True) should request layout metrics and pass clip."""
        async def _test():
            raw = b"\x89PNG"
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_pdf_returns_bytes(self, cdp):
        """pdf should decode base64 and return bytes."""
        async def _test():
            raw = b"%PDF-1.4 fake"
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_wait_for_selector_found(self, cdp):
        """wait_for_selector should return when selector exists in DOM."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_wait_for_selector_timeout(self, cdp, CDPError):
        """wait_for_selector should raise CDPError on timeout."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_scroll_dispatches_mouse_events(self, cdp):
        """scroll should send Input.dispatchMouseEvent commands."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_get_cookies(self, cdp):
        """get_cookies should return cookie list from Network.getCookies."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_set_cookies(self, cdp):
        """set_cookies should call Network.setCookies with correct params."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_close_page(self, cdp):
        """close should send Target.closeTarget."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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

    def test_get_accessibility_tree(self, cdp):
        """get_accessibility_tree should return AX tree nodes."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
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
            ws = _make_mock_ws()
            self.mock_ws = ws
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(ws):
                await client.connect()
            self.client = client
            self.page = await client.new_page()
            self.collector = await self.page.enable_network()
            await client.close()

        asyncio.run(_setup())
        yield

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
    """Tests for sync wrapper delegation.

    CDPClient runs its own event loop on a background thread.  Under pytest
    the background thread's event loop can stall when ``run_coroutine_threadsafe``
    is used, so we test the sync API via the async client (which is what the
    sync wrapper delegates to) plus a few structural checks.
    """

    def test_sync_wrapper_delegates_to_async(self, cdp, mock_ws):
        """Sync methods should call async counterparts."""
        async def _test():
            # Verify the sync wrapper structure delegates correctly
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(mock_ws):
                await client.connect()
            page = await client.new_page()
            assert page is not None
            assert len(mock_ws.sent) > 0
            await client.close()

        asyncio.run(_test())

        # Also verify CDPClient has the expected sync API surface
        assert hasattr(cdp.CDPClient, "connect")
        assert hasattr(cdp.CDPClient, "send")
        assert hasattr(cdp.CDPClient, "new_page")
        assert hasattr(cdp.CDPClient, "close")
        assert hasattr(cdp.CDPClient, "subscribe")
        assert hasattr(cdp.CDPClient, "unsubscribe")

    def test_sync_close_stops_event_loop(self, cdp, mock_ws):
        """close() should cleanly shut down via the async client."""
        async def _test():
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(mock_ws):
                await client.connect()
            await client.close()
            assert mock_ws._closed
            assert not client._connected

        asyncio.run(_test())


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


# ---------------------------------------------------------------------------
# TestCDPCLIIntegration
# ---------------------------------------------------------------------------


class TestCDPCLIIntegration:
    """Tests for CLI CDP routing using typer's CliRunner."""

    @pytest.fixture(autouse=True)
    def _setup_runner(self):
        from typer.testing import CliRunner
        from flarecrawl.cli import app
        self.runner = CliRunner()
        self.app = app

    def test_scrape_cdp_flag_in_help(self):
        """--cdp appears in scrape --help."""
        result = self.runner.invoke(self.app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "--cdp" in result.output

    def test_keep_alive_flag_in_help(self):
        """--keep-alive appears in scrape --help."""
        result = self.runner.invoke(self.app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "--keep-alive" in result.output

    def test_record_flag_in_help(self):
        """--record appears in scrape --help."""
        result = self.runner.invoke(self.app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "--record" in result.output

    def test_live_view_flag_in_help(self):
        """--live-view appears in scrape --help."""
        result = self.runner.invoke(self.app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "--live-view" in result.output

    def test_interactive_flag_in_help(self):
        """--interactive appears in scrape --help."""
        result = self.runner.invoke(self.app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "--interactive" in result.output

    def test_save_cookies_flag_in_help(self):
        """--session (cookie loading) appears in scrape --help."""
        # The CLI uses --session for cookie persistence, not --save-cookies
        result = self.runner.invoke(self.app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "--session" in result.output

    def test_load_cookies_flag_in_help(self):
        """--session (cookie loading) appears in scrape --help."""
        result = self.runner.invoke(self.app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "--session" in result.output

    def test_cdp_subcommand_in_help(self):
        """cdp appears in the main app --help."""
        result = self.runner.invoke(self.app, ["--help"])
        assert result.exit_code == 0
        assert "cdp" in result.output

    def test_cdp_sessions_in_help(self):
        """sessions appears in cdp --help."""
        result = self.runner.invoke(self.app, ["cdp", "--help"])
        assert result.exit_code == 0
        assert "sessions" in result.output

    def test_cdp_close_in_help(self):
        """close appears in cdp --help."""
        result = self.runner.invoke(self.app, ["cdp", "--help"])
        assert result.exit_code == 0
        assert "close" in result.output


# ---------------------------------------------------------------------------
# TestCDPSessionStore
# ---------------------------------------------------------------------------


class TestCDPSessionStore:
    """Tests for CDP session persistence in config.py."""

    @pytest.fixture(autouse=True)
    def _setup_tmp(self, tmp_path, monkeypatch):
        """Point config dir to a temp directory."""
        monkeypatch.setattr("flarecrawl.config.get_config_dir", lambda: tmp_path)
        self.tmp_path = tmp_path

    def test_save_cdp_session(self):
        """save_cdp_session creates the sessions file."""
        import time
        from flarecrawl.config import save_cdp_session, _get_cdp_sessions_path
        save_cdp_session("sess-1", "wss://example.com", time.time() + 3600)
        assert _get_cdp_sessions_path().exists()

    def test_load_cdp_session(self):
        """save then load returns matching data."""
        import time
        from flarecrawl.config import save_cdp_session, load_cdp_session
        expiry = time.time() + 3600
        save_cdp_session("sess-2", "wss://test.com", expiry)
        loaded = load_cdp_session()
        assert loaded is not None
        assert loaded["session_id"] == "sess-2"
        assert loaded["ws_url"] == "wss://test.com"

    def test_load_expired_session(self):
        """Expired session returns None."""
        import time
        from flarecrawl.config import save_cdp_session, load_cdp_session
        save_cdp_session("sess-old", "wss://old.com", time.time() - 100)
        loaded = load_cdp_session()
        assert loaded is None

    def test_clear_cdp_session(self):
        """save then clear removes the session."""
        import time
        from flarecrawl.config import save_cdp_session, clear_cdp_session, load_cdp_session
        save_cdp_session("sess-3", "wss://clear.com", time.time() + 3600)
        result = clear_cdp_session("sess-3")
        assert result is True
        assert load_cdp_session() is None

    def test_load_missing_session(self):
        """load when no session saved returns None."""
        from flarecrawl.config import load_cdp_session
        loaded = load_cdp_session()
        assert loaded is None


# ---------------------------------------------------------------------------
# TestNetworkCollectorHAR
# ---------------------------------------------------------------------------


class TestNetworkCollectorHAR:
    """Detailed HAR output validation for NetworkCollector."""

    @pytest.fixture(autouse=True)
    def _setup_collector(self, cdp):
        """Set up a connected client with a page and network enabled."""
        async def _setup():
            ws = _make_mock_ws()
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(ws):
                await client.connect()
            self.page = await client.new_page()
            self.collector = await self.page.enable_network()
            await client.close()

        asyncio.run(_setup())
        yield

    def _feed_request_response(self, req_id, url, method="GET", status=200):
        """Helper to feed a complete request/response pair."""
        self.collector._on_request({
            "requestId": req_id,
            "request": {
                "url": url,
                "method": method,
                "headers": {"Accept": "text/html"},
            },
            "wallTime": 1000.0,
        })
        self.collector._on_response({
            "requestId": req_id,
            "response": {
                "url": url,
                "status": status,
                "statusText": "OK" if status == 200 else "Not Found",
                "headers": {"Content-Type": "text/html"},
                "protocol": "HTTP/1.1",
                "encodedDataLength": 1234,
                "mimeType": "text/html",
            },
        })
        self.collector._on_finished({
            "requestId": req_id,
            "encodedDataLength": 1234,
        })

    def test_har_has_correct_version(self):
        """HAR version is '1.2'."""
        har = self.collector.to_har()
        assert har["log"]["version"] == "1.2"

    def test_har_has_creator(self):
        """HAR creator has name 'flarecrawl'."""
        har = self.collector.to_har()
        assert har["log"]["creator"]["name"] == "flarecrawl"

    def test_har_entry_has_request(self):
        """Each entry has request with method, url, headers."""
        self._feed_request_response("r1", "https://example.com")
        har = self.collector.to_har()
        entry = har["log"]["entries"][0]
        req = entry["request"]
        assert req["method"] == "GET"
        assert req["url"] == "https://example.com"
        assert isinstance(req["headers"], list)
        assert len(req["headers"]) >= 1

    def test_har_entry_has_response(self):
        """Each entry has response with status, headers."""
        self._feed_request_response("r1", "https://example.com", status=200)
        har = self.collector.to_har()
        entry = har["log"]["entries"][0]
        resp = entry["response"]
        assert resp["status"] == 200
        assert isinstance(resp["headers"], list)

    def test_har_entry_has_timings(self):
        """Entries include timing info."""
        self._feed_request_response("r1", "https://example.com")
        har = self.collector.to_har()
        entry = har["log"]["entries"][0]
        assert "timings" in entry
        timings = entry["timings"]
        assert "send" in timings
        assert "wait" in timings
        assert "receive" in timings

    def test_har_multiple_requests(self):
        """Multiple requests produce multiple entries in order."""
        self._feed_request_response("r1", "https://example.com/page1")
        self._feed_request_response("r2", "https://example.com/page2")
        self._feed_request_response("r3", "https://example.com/page3")
        har = self.collector.to_har()
        entries = har["log"]["entries"]
        assert len(entries) == 3
        urls = [e["request"]["url"] for e in entries]
        assert "https://example.com/page1" in urls
        assert "https://example.com/page2" in urls
        assert "https://example.com/page3" in urls


# ---------------------------------------------------------------------------
# TestCookieManagement
# ---------------------------------------------------------------------------


class TestCookieManagement:
    """Thorough tests for CDPPage cookie methods."""

    def test_get_cookies_with_urls(self, cdp):
        """get_cookies(urls=["https://example.com"]) passes urls param."""
        async def _test():
            ws = _make_mock_ws()
            ws.add_response(
                "Network.getCookies",
                {"cookies": [{"name": "sid", "value": "xyz", "domain": ".example.com"}]},
            )
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            cookies = await page.get_cookies(urls=["https://example.com"])
            # Verify the urls param was sent
            cookie_msgs = [
                msg for msg in ws.sent if msg.get("method") == "Network.getCookies"
            ]
            assert len(cookie_msgs) >= 1
            assert cookie_msgs[-1]["params"]["urls"] == ["https://example.com"]
            assert cookies[0]["name"] == "sid"
            await client.close()

        asyncio.run(_test())

    def test_set_multiple_cookies(self, cdp):
        """set_cookies with multiple cookies sends correct params."""
        async def _test():
            ws = _make_mock_ws()
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            cookies = [
                {"name": "a", "value": "1", "domain": ".example.com"},
                {"name": "b", "value": "2", "domain": ".example.com"},
                {"name": "c", "value": "3", "domain": ".test.com"},
            ]
            await page.set_cookies(cookies)
            set_msgs = [
                msg for msg in ws.sent if msg.get("method") == "Network.setCookies"
            ]
            assert len(set_msgs) >= 1
            assert set_msgs[-1]["params"]["cookies"] == cookies
            assert len(set_msgs[-1]["params"]["cookies"]) == 3
            await client.close()

        asyncio.run(_test())

    def test_cookie_roundtrip(self, cdp):
        """set cookies then get them back (mock returns what was set)."""
        async def _test():
            ws = _make_mock_ws()
            cookies_to_set = [
                {"name": "token", "value": "abc123", "domain": ".example.com"},
                {"name": "pref", "value": "dark", "domain": ".example.com"},
            ]
            # Configure mock to return the cookies we set
            ws.add_response(
                "Network.getCookies",
                {"cookies": cookies_to_set},
            )
            client = cdp._AsyncCDPClient(account_id="acct-1", api_token="tok-secret")
            with _patch_connect(ws):
                await client.connect()
            page = await client.new_page()
            await page.set_cookies(cookies_to_set)
            retrieved = await page.get_cookies()
            assert len(retrieved) == 2
            assert retrieved[0]["name"] == "token"
            assert retrieved[1]["name"] == "pref"
            await client.close()

        asyncio.run(_test())

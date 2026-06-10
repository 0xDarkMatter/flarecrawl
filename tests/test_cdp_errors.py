"""Unit tests for CDP error enrichment helper."""

from flarecrawl.client import FlareCrawlError
from flarecrawl.cli import _enrich_cdp_error


class TestEnrichCdpError:
    """Tests for _enrich_cdp_error()."""

    def test_bot_detection(self):
        e = FlareCrawlError("execution context was destroyed", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "Suggestions:" in str(enriched)
        assert "--stealth" in str(enriched)
        assert "--paywall" in str(enriched)

    def test_navigation_error(self):
        e = FlareCrawlError("Navigation failed: frame detached", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--stealth" in str(enriched)

    def test_detached_frame(self):
        e = FlareCrawlError("Target detached from page", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--stealth" in str(enriched)

    def test_timeout(self):
        e = FlareCrawlError("Page timed out after 30s", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--timeout 60000" in str(enriched)

    def test_timeout_variant(self):
        e = FlareCrawlError("Timeout waiting for selector", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--timeout 60000" in str(enriched)

    def test_redirect(self):
        e = FlareCrawlError("Too many redirect hops", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--browser-cookies" in str(enriched)

    def test_network_error(self):
        e = FlareCrawlError("Network error: net::ERR_CONNECTION_REFUSED", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--proxy" in str(enriched)

    def test_connection_error(self):
        e = FlareCrawlError("Connection reset by peer", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--proxy" in str(enriched)

    def test_websocket_error(self):
        e = FlareCrawlError("WebSocket connection closed", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "flarecrawl cdp sessions" in str(enriched)

    def test_auth_401(self):
        e = FlareCrawlError("Server returned 401 Unauthorized", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--interactive" in str(enriched)

    def test_auth_403(self):
        e = FlareCrawlError("Server returned 403 Forbidden", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--interactive" in str(enriched)

    def test_cookies_error(self):
        e = FlareCrawlError("Cookies expired or invalid", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--browser-cookies" in str(enriched)

    def test_no_match_returns_unchanged(self):
        e = FlareCrawlError("Something completely unknown", code="CDP_ERROR")
        result = _enrich_cdp_error(e)
        assert result is e  # exact same object
        assert "Suggestions:" not in str(result)

    def test_preserves_code(self):
        e = FlareCrawlError("execution context was destroyed", code="CUSTOM_CODE")
        enriched = _enrich_cdp_error(e)
        assert enriched.code == "CUSTOM_CODE"

    def test_preserves_code_on_timeout(self):
        e = FlareCrawlError("Timeout connecting", code="TIMEOUT")
        enriched = _enrich_cdp_error(e)
        assert enriched.code == "TIMEOUT"

    def test_multiple_matches(self):
        e = FlareCrawlError(
            "Network error: connection timed out at auth endpoint",
            code="CDP_ERROR",
        )
        enriched = _enrich_cdp_error(e)
        msg = str(enriched)
        assert "--timeout 60000" in msg
        assert "--proxy" in msg
        assert "--interactive" in msg

    def test_case_insensitive(self):
        e = FlareCrawlError("EXECUTION CONTEXT was DESTROYED", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "Suggestions:" in str(enriched)

    def test_url_parameter_accepted(self):
        e = FlareCrawlError("Timeout waiting for page", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e, url="https://example.com")
        assert "--timeout 60000" in str(enriched)
        assert enriched.code == "CDP_ERROR"


class TestCloseIdempotency:
    """v0.25.2: CDPClient.close() must be idempotent and not leak coroutine warnings."""

    def test_double_close_is_safe(self):
        """Calling close() twice should be a no-op the second time."""
        import asyncio
        import warnings
        from unittest.mock import MagicMock, patch

        from flarecrawl.cdp import CDPClient

        with patch("flarecrawl.cdp.websockets") as ws_mod:
            # Stub out the connect path so we don't hit network
            ws_mod.asyncio.client.connect = MagicMock()

            client = CDPClient(account_id="acct", api_token="tok")
            with warnings.catch_warnings():
                warnings.simplefilter("error")  # Promote RuntimeWarning to error
                client.close()
                client.close()  # second call should be a no-op
                # If a coroutine was leaked, the warning would be raised here

    def test_run_after_close_raises_runtime_error_not_unawaited_warning(self):
        """Once closed, _run() should raise RuntimeError, not silently leak a coro."""
        import warnings
        from unittest.mock import MagicMock, patch

        from flarecrawl.cdp import CDPClient

        with patch("flarecrawl.cdp.websockets") as ws_mod:
            ws_mod.asyncio.client.connect = MagicMock()

            client = CDPClient(account_id="acct", api_token="tok")
            client.close()

            async def some_coro():
                return 42

            coro = some_coro()
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                try:
                    client._run(coro)
                    raise AssertionError("expected RuntimeError")
                except RuntimeError as e:
                    assert "stopped" in str(e).lower()


class TestKeepAliveConversion:
    """Cloudflare's CDP keep_alive param expects milliseconds with a 10s minimum.

    Regression for the v0.22.x bug where flarecrawl sent seconds (e.g. 60),
    which CF rejected with HTTP 400.
    """

    def test_seconds_to_ms_conversion(self):
        from flarecrawl.cdp import MIN_KEEP_ALIVE_MS

        # Sanity: the constant exists and equals 10s
        assert MIN_KEEP_ALIVE_MS == 10_000

    def test_keep_alive_url_construction(self):
        """The connect() URL should encode keep_alive in milliseconds."""
        # We test the conversion logic by inspecting the URL the connect()
        # path constructs. Mock at the websockets boundary.
        import asyncio
        from unittest.mock import AsyncMock, patch

        from flarecrawl.cdp import _AsyncCDPClient

        captured: dict = {}

        async def fake_connect(url, **kwargs):
            captured["url"] = url
            raise RuntimeError("stop here")

        with patch("flarecrawl.cdp.websockets") as ws_mod:
            ws_mod.asyncio.client.connect = AsyncMock(side_effect=fake_connect)

            client = _AsyncCDPClient(account_id="acct", api_token="tok")
            try:
                asyncio.run(client.connect(keep_alive=60))
            except Exception:
                pass

        # Should send keep_alive=60000 (60s × 1000), not keep_alive=60
        assert "keep_alive=60000" in captured.get("url", ""), captured

    def test_keep_alive_minimum_enforced(self):
        """Values below 10s should bump up to the 10s minimum."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        from flarecrawl.cdp import _AsyncCDPClient

        captured: dict = {}

        async def fake_connect(url, **kwargs):
            captured["url"] = url
            raise RuntimeError("stop")

        with patch("flarecrawl.cdp.websockets") as ws_mod:
            ws_mod.asyncio.client.connect = AsyncMock(side_effect=fake_connect)
            client = _AsyncCDPClient(account_id="acct", api_token="tok")
            try:
                asyncio.run(client.connect(keep_alive=5))  # 5s, below 10s min
            except Exception:
                pass

        # 5s × 1000 = 5000ms, which is below MIN_KEEP_ALIVE_MS (10000),
        # so it should be bumped to 10000.
        assert "keep_alive=10000" in captured.get("url", ""), captured


class TestJsEvalAutoPromotion:
    """v0.23.0 P1.2: --js-eval should auto-promote to --cdp.

    Without CDP, the REST /scrape endpoint silently drops the eval return
    value. Promoting matches the existing pattern for --interactive,
    --live-view, --record, --keep-alive, etc.
    """

    def test_js_eval_invokes_cdp_path(self):
        from typer.testing import CliRunner

        from flarecrawl.cli import app

        runner = CliRunner()
        # Use a fake URL + force a quick error so we don't hit the network.
        # We just want to verify the flag plumbing routed to CDP, not
        # actually run the eval. Mock the CDP client constructor.
        from unittest.mock import MagicMock, patch

        with (
            patch("flarecrawl.cli.scrape.Client") as cli_client,
            patch("flarecrawl.cli.scrape.console") as cons,
        ):
            cli_client.return_value = MagicMock()
            # Force scrape to bail early but capture cdp= passed to client calls
            cli_client.return_value.scrape.side_effect = SystemExit(0)
            try:
                runner.invoke(
                    app,
                    [
                        "scrape",
                        "https://example.com",
                        "--js-eval",
                        "1+1",
                    ],
                )
            except SystemExit:
                pass

            # The auto-promote message should print when not in --json mode
            printed = "\n".join(
                "".join(str(a) for a in call.args) for call in cons.print.call_args_list
            )
            assert "auto-promoting to --cdp for --js-eval" in printed, printed

    def test_js_eval_with_json_silent_promotion(self):
        """--json mode shouldn't print the promotion notice (would corrupt JSON)."""
        from unittest.mock import MagicMock, patch

        from typer.testing import CliRunner

        from flarecrawl.cli import app

        runner = CliRunner()
        with (
            patch("flarecrawl.cli.scrape.Client") as cli_client,
            patch("flarecrawl.cli.scrape.console") as cons,
        ):
            cli_client.return_value = MagicMock()
            cli_client.return_value.scrape.side_effect = SystemExit(0)
            try:
                runner.invoke(
                    app,
                    ["scrape", "https://example.com", "--js-eval", "1+1", "--json"],
                )
            except SystemExit:
                pass
            printed = "\n".join(
                "".join(str(a) for a in call.args) for call in cons.print.call_args_list
            )
            assert "auto-promoting" not in printed


class TestErrorClassification:
    """v0.23.0: CDP errors map to specific exception types and exit codes."""

    def test_auth_error_class_and_code(self):
        from flarecrawl.cdp import CDPAuthError

        e = CDPAuthError("token rejected", http_status=401)
        assert e.code == "CDP_AUTH_ERROR"
        assert e.http_status == 401

    def test_tier_error_class_and_code(self):
        from flarecrawl.cdp import CDPTierError

        e = CDPTierError("paid tier required", http_status=404)
        assert e.code == "CDP_TIER_ERROR"
        assert e.http_status == 404

    def test_connection_error_carries_status(self):
        from flarecrawl.cdp import CDPConnectionError

        e = CDPConnectionError("generic", http_status=400)
        assert e.code == "CDP_CONNECTION_ERROR"
        assert e.http_status == 400

    def test_cli_exit_code_mapping_for_auth(self):
        import typer

        from flarecrawl.cdp import CDPAuthError, CDPTierError
        from flarecrawl.cli import EXIT_AUTH_REQUIRED, EXIT_FORBIDDEN, _handle_api_error

        for cls, expected_code in [
            (CDPAuthError, EXIT_AUTH_REQUIRED),
            (CDPTierError, EXIT_FORBIDDEN),
        ]:
            err = cls("x", http_status=401)
            try:
                _handle_api_error(err, as_json=True)
                raise AssertionError("should have exited")
            except typer.Exit as exc:
                assert exc.exit_code == expected_code, (
                    f"{cls.__name__} should exit with {expected_code}, got {exc.exit_code}"
                )

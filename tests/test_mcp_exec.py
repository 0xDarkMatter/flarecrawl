"""Test the MCP execution layer (_exec.py).

Tests (all with CLI runner MOCKED):
- max_chars truncation sets meta.truncated
- agent_safe flag injected for T1/T2 but not T3
- exit-code → error envelope mapping (2→AUTH_REQUIRED etc.) with next_steps non-empty
- meta.blocked akamai → next_steps suggests stealth/p6
- terminal cf_1020 → next_steps says do-not-retry
- binary tools return path not base64
- T3 options dict → flag mapping incl. bool/list/underscore conversion + collision rejection
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: build a mock CliRunner result
# ---------------------------------------------------------------------------


def _make_runner_result(stdout: str = "", exit_code: int = 0):
    mock = MagicMock()
    mock.exit_code = exit_code
    mock.output = stdout
    return mock


def _json_result(data: dict[str, Any]) -> str:
    import json
    return json.dumps(data)


# ---------------------------------------------------------------------------
# Truncation tests
# ---------------------------------------------------------------------------


class TestTruncation:
    def test_max_chars_truncation_sets_meta_truncated(self):
        from flarecrawl.mcp_tools._exec import _apply_truncation

        long_text = "A" * 100 + "\n" + "B" * 100
        envelope = {"ok": True, "data": {"content": long_text}, "meta": {}}
        result = _apply_truncation(envelope, max_chars=50)

        assert result["meta"]["truncated"] is True
        assert result["meta"]["chars_total"] == len(long_text)
        assert len(result["data"]["content"]) <= 50

    def test_no_truncation_when_within_limit(self):
        from flarecrawl.mcp_tools._exec import _apply_truncation

        envelope = {"ok": True, "data": {"content": "short text"}, "meta": {}}
        result = _apply_truncation(envelope, max_chars=100)
        assert "truncated" not in result["meta"]

    def test_truncation_at_line_boundary(self):
        from flarecrawl.mcp_tools._exec import _apply_truncation

        text = "Line 1\nLine 2\nLine 3\nLine 4"
        # 50 chars max — should cut at newline
        envelope = {"ok": True, "data": {"content": text}, "meta": {}}
        result = _apply_truncation(envelope, max_chars=15)
        content = result["data"]["content"]
        assert not content.endswith("\n")  # no trailing newline
        assert "\n" in content or len(content) <= 15

    def test_no_truncation_when_none(self):
        from flarecrawl.mcp_tools._exec import _apply_truncation

        long = "A" * 10000
        envelope = {"ok": True, "data": {"content": long}, "meta": {}}
        result = _apply_truncation(envelope, max_chars=None)
        assert result["data"]["content"] == long

    def test_list_data_truncation(self):
        from flarecrawl.mcp_tools._exec import _apply_truncation

        items = [{"content": "A" * 100}, {"content": "B" * 100}]
        envelope = {"ok": True, "data": items, "meta": {}}
        result = _apply_truncation(envelope, max_chars=80)
        assert result["meta"]["truncated"] is True


# ---------------------------------------------------------------------------
# Exit code → error envelope mapping
# ---------------------------------------------------------------------------


class TestExitCodeMapping:
    def _run(self, exit_code: int):
        from flarecrawl.mcp_tools._exec import run_cli

        runner_result = _make_runner_result("error output", exit_code=exit_code)
        with patch("flarecrawl.mcp_tools._exec._get_runner") as mock_runner_cls, \
             patch("flarecrawl.mcp_tools._exec._get_app"):
            mock_runner = MagicMock()
            mock_runner.invoke.return_value = runner_result
            mock_runner_cls.return_value = mock_runner
            return run_cli(["scrape", "https://example.com", "--json"], tool_name="test")

    def test_exit_0_returns_ok(self):
        from flarecrawl.mcp_tools._exec import run_cli
        import json

        payload = json.dumps({"ok": True, "data": {"content": "hello"}, "meta": {}})
        runner_result = _make_runner_result(payload, exit_code=0)
        with patch("flarecrawl.mcp_tools._exec._get_runner") as mock_runner_cls, \
             patch("flarecrawl.mcp_tools._exec._get_app"):
            mock_runner = MagicMock()
            mock_runner.invoke.return_value = runner_result
            mock_runner_cls.return_value = mock_runner
            result = run_cli(["scrape", "https://example.com", "--json"])
        assert result.get("ok") is True

    def test_exit_2_returns_auth_required(self):
        result = self._run(2)
        assert result["ok"] is False
        assert result["error"]["code"] == "AUTH_REQUIRED"
        assert result["error"]["category"] == "permission_denied"

    def test_exit_2_next_steps_non_empty(self):
        result = self._run(2)
        assert len(result["error"]["next_steps"]) > 0

    def test_exit_2_next_steps_mention_auth_login(self):
        result = self._run(2)
        all_text = str(result["error"]["next_steps"])
        assert "auth login" in all_text.lower() or "auth" in all_text.lower()

    def test_exit_7_returns_rate_limited(self):
        result = self._run(7)
        assert result["ok"] is False
        assert result["error"]["code"] == "RATE_LIMITED"

    def test_exit_7_next_steps_mention_retry(self):
        result = self._run(7)
        all_text = str(result["error"]["next_steps"])
        assert "retry" in all_text.lower() or "wait" in all_text.lower()

    def test_exit_1_returns_upstream_error(self):
        result = self._run(1)
        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_ERROR"


# ---------------------------------------------------------------------------
# agent_safe injection
# ---------------------------------------------------------------------------


class TestAgentSafeInjection:
    def _capture_args(self, cli_args, inject: bool):
        """Return the args that would be passed to invoke."""
        from flarecrawl.mcp_tools._exec import run_cli
        import json

        payload = json.dumps({"ok": True, "data": {"content": "x"}, "meta": {}})
        runner_result = _make_runner_result(payload, exit_code=0)

        captured = []
        with patch("flarecrawl.mcp_tools._exec._get_runner") as mock_runner_cls, \
             patch("flarecrawl.mcp_tools._exec._get_app"):
            mock_runner = MagicMock()
            def side_effect(app, args, **kwargs):
                captured.extend(args)
                return runner_result
            mock_runner.invoke.side_effect = side_effect
            mock_runner_cls.return_value = mock_runner
            run_cli(cli_args, inject_agent_safe=inject)
        return captured

    def test_agent_safe_injected_when_requested(self):
        args = self._capture_args(["scrape", "https://x.com", "--json"], inject=True)
        assert "--agent-safe" in args

    def test_agent_safe_not_injected_when_off(self):
        args = self._capture_args(["scrape", "https://x.com", "--json"], inject=False)
        assert "--agent-safe" not in args

    def test_agent_safe_not_duplicated(self):
        args = self._capture_args(
            ["scrape", "https://x.com", "--json", "--agent-safe"], inject=True
        )
        assert args.count("--agent-safe") == 1


# ---------------------------------------------------------------------------
# meta.blocked → next_steps
# ---------------------------------------------------------------------------


class TestBlockedVerdictNextSteps:
    def test_akamai_suggests_stealth_and_p6(self):
        from flarecrawl.mcp_tools._errors import blocked_error

        blocked = {"detected": True, "vendor": "akamai", "kind": "bot_wall", "terminal": False}
        result = blocked_error(blocked, "scrape_raw")

        assert result["ok"] is False
        next_steps = result["error"]["next_steps"]
        all_text = str(next_steps).lower()
        assert "stealth" in all_text or "p6_raw" in all_text

    def test_datadome_suggests_stealth(self):
        from flarecrawl.mcp_tools._errors import blocked_error

        blocked = {"detected": True, "vendor": "datadome", "kind": "bot_wall", "terminal": False}
        result = blocked_error(blocked, "scrape_raw")
        assert result["ok"] is False

    def test_cf_1020_terminal_no_escalation(self):
        from flarecrawl.mcp_tools._errors import blocked_error

        blocked = {"detected": True, "vendor": "cloudflare", "kind": "cf_1020_hard", "terminal": True}
        result = blocked_error(blocked, "scrape_raw")
        assert result["ok"] is False
        next_steps_text = str(result["error"]["next_steps"]).lower()
        # Should say do-not-retry / non-bypassable
        assert "non-bypassable" in next_steps_text or "do not retry" in next_steps_text or "terminal" in next_steps_text

    def test_terminal_true_no_escalation(self):
        from flarecrawl.mcp_tools._errors import blocked_error

        blocked = {"detected": True, "vendor": "cloudflare", "kind": "generic", "terminal": True}
        result = blocked_error(blocked, "scrape_raw")
        next_steps = result["error"]["next_steps"]
        all_text = str(next_steps).lower()
        assert "do not retry" in all_text or "non-bypassable" in all_text or "terminal" in all_text


# ---------------------------------------------------------------------------
# Binary output: path not base64
# ---------------------------------------------------------------------------


class TestBinaryOutput:
    def test_binary_tool_returns_path_and_bytes(self, tmp_path):
        from flarecrawl.mcp_tools._exec import run_cli

        # Create a fake output file
        fake_file = tmp_path / "screenshot.png"
        fake_file.write_bytes(b"\x89PNG" + b"\x00" * 100)

        runner_result = _make_runner_result("", exit_code=0)
        with patch("flarecrawl.mcp_tools._exec._get_runner") as mock_runner_cls, \
             patch("flarecrawl.mcp_tools._exec._get_app"):
            mock_runner = MagicMock()
            mock_runner.invoke.return_value = runner_result
            mock_runner_cls.return_value = mock_runner
            result = run_cli(
                ["screenshot", "https://x.com", "-o", str(fake_file)],
                binary_output_path=str(fake_file),
            )

        assert result.get("ok") is True
        assert "path" in result["data"]
        assert "bytes" in result["data"]
        assert "base64" not in str(result)

    def test_binary_tool_no_content_in_result(self, tmp_path):
        from flarecrawl.mcp_tools._exec import run_cli

        fake_file = tmp_path / "output.pdf"
        fake_file.write_bytes(b"%PDF" + b"\x00" * 50)

        runner_result = _make_runner_result("", exit_code=0)
        with patch("flarecrawl.mcp_tools._exec._get_runner") as mock_runner_cls, \
             patch("flarecrawl.mcp_tools._exec._get_app"):
            mock_runner = MagicMock()
            mock_runner.invoke.return_value = runner_result
            mock_runner_cls.return_value = mock_runner
            result = run_cli(
                ["pdf", "https://x.com", "-o", str(fake_file)],
                binary_output_path=str(fake_file),
            )

        assert result["data"]["bytes"] == fake_file.stat().st_size


# ---------------------------------------------------------------------------
# T3 options dict → flag conversion
# ---------------------------------------------------------------------------


class TestOptionsToFlags:
    def test_underscore_to_hyphen(self):
        from flarecrawl.mcp_tools._exec import _options_to_flags
        flags = _options_to_flags({"wait_until": "networkidle2"}, set())
        assert "--wait-until" in flags
        assert "networkidle2" in flags

    def test_bool_true_is_bare_flag(self):
        from flarecrawl.mcp_tools._exec import _options_to_flags
        flags = _options_to_flags({"stealth": True}, set())
        assert "--stealth" in flags
        assert "True" not in flags

    def test_bool_false_is_skipped(self):
        from flarecrawl.mcp_tools._exec import _options_to_flags
        flags = _options_to_flags({"stealth": False}, set())
        assert "--stealth" not in flags

    def test_list_is_repeated_flag(self):
        from flarecrawl.mcp_tools._exec import _options_to_flags
        flags = _options_to_flags({"target": ["url1", "url2"]}, set())
        assert flags.count("--target") == 2
        assert "url1" in flags
        assert "url2" in flags

    def test_none_value_skipped(self):
        from flarecrawl.mcp_tools._exec import _options_to_flags
        flags = _options_to_flags({"output": None}, set())
        assert "--output" not in flags

    def test_collision_raises_value_error(self):
        from flarecrawl.mcp_tools._exec import _options_to_flags
        with pytest.raises(ValueError, match="collides"):
            _options_to_flags({"url": "https://x.com"}, {"url"})

    def test_collision_with_hyphen_key(self):
        from flarecrawl.mcp_tools._exec import _options_to_flags
        with pytest.raises(ValueError, match="collides"):
            _options_to_flags({"wait-until": "val"}, {"wait-until"})

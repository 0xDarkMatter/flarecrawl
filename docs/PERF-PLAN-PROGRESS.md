# Performance Plan Progress (v0.16.0 / v0.17.0)

Branch: `perf/items-1-17`
Baseline: **746 passed, 5 failed (pre-existing live+rules), 10 skipped** on `main @ ef19b74`.
Methodology: DSP (Design → Spec → Produce) per item, one commit each.

## Pre-existing failures (not caused by this work)
- `tests/live/test_interact_live.py::*` (3) — require network/Cloudflare access
- `tests/test_rules.py::TestRulesCliCommands::test_rules_path` — path assertion

---

## Item 1 — uvloop conditional import

**Design**
- Add `uvloop>=0.19.0; sys_platform != 'win32'` to new `perf` optional dep group.
- In `cli.py`, attempt `import uvloop; uvloop.install()` at module top, guarded by `try/except ImportError` and `sys.platform != "win32"`.
- No behaviour change on Windows (worktree OS) — just a best-effort install on Linux/macOS.
- Risk: low. uvloop is a drop-in asyncio policy.

**Spec**
- Acceptance: `flarecrawl --help` still runs on Windows; if `uvloop` importable (non-win32), `asyncio.get_event_loop_policy()` is `uvloop.EventLoopPolicy`.
- Test: `tests/test_uvloop_bootstrap.py::test_uvloop_noop_on_windows` — import cli, assert no crash.

**Produce** — see commit.

---

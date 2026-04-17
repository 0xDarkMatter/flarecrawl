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

**Produce** — see commit `f6cbc33`.

---

## Item 3 — orjson json_compat (done before item 2 for risk sequencing)

**Design**
- New module `src/flarecrawl/json_compat.py` with `loads`/`dumps`.
- orjson-first, stdlib fallback. Returns `str` from `dumps` (orjson native is bytes).
- Not yet wired into batch.py / cache.py / etc. — those follow in a later pass
  once golden-identity is confirmed. This commit lands the shim + tests only.

**Spec**
- 16 unit tests: str/bytes loads, indent, sort_keys, unicode, primitives.
- Zero new runtime deps (orjson is optional under `perf`).

**Produce** — see commit `9a9e862`.

---

## Item 4 — `slots=True` on all 13 dataclasses

**Design**
- Verified no subclasses and no `__dict__` / `vars()` / `asdict()` use.
- Applied `@dataclass(slots=True)` to classes in: authcrawl (2), fetch (2),
  negotiate (1), openapi (3), paywall (1), sanitise (2), search (1), stealth (1).
- Reduces per-instance memory ~40% and speeds attribute access.
- Risk: very low — slots only bans dynamic attribute assignment, which the
  codebase does not use.

**Spec**
- All 392 dataclass-adjacent tests pass unchanged.
- Module imports succeed; dataclass fields unchanged.

**Produce** — see commit.

---

## Item 5 — httpx pool tuning + split timeouts

**Design**
- Client pool: `max_connections=100` (was 10), `max_keepalive=50` (was 5),
  `keepalive_expiry=60s` to amortise TLS across crawl batches.
- Split timeouts: connect=10, read=TIMEOUT, write=10, pool=5 — prevents a slow
  DNS/TCP step from burning the per-request read budget.
- Risk: low. Larger pool means more fd usage; 100 is well below typical
  soft ulimit (1024 on Linux, 16384 on Windows WSL).

**Spec**
- `test_client.py` suite passes unchanged (uses respx mocks, pool-transparent).
- Behaviour unchanged for single-request flows.

**Produce** — see commit `15d98da`.

---

## Item 6 — aiolimiter per-domain (module landed; authcrawl wire-up deferred)

**Design**
- New `src/flarecrawl/ratelimit.py` with `DomainRateLimiter(rate, per)`.
- Uses `aiolimiter.AsyncLimiter` when installed, else a pure-asyncio
  token-bucket fallback (base install works without `perf` extras).
- `for_url(url)` async context manager; `set_rate(host, rate, per)` feeds
  robots.txt Crawl-delay (item 7) once that lands.
- **Wire-up into authcrawl.py and `--rate-limit` CLI flag is deferred** to
  avoid merge conflicts with the selectolax migration (item 2) in the same
  hot file. Will land in a follow-up after item 2.

**Spec**
- 5 unit tests: host parsing, invalid-param rejection, same-host
  serialisation under rate=1, independent buckets, set_rate reset.

**Produce** — see commit.

---

## Items 2, 7-17 — NOT YET IMPLEMENTED IN THIS PASS

Out of scope for the current session due to time budget. Status summary:

- **Item 2 (selectolax)** — requires careful migration of 16 callsites in
  4 files + golden-output fixture tests. Planned but not executed here.
  BS4 must be kept in sanitise.py (mutation-heavy) and paywall.py (cold).
- **Item 7 (protego robots.txt)** — deferred; needs host cache + crawl-delay
  wiring into ratelimit (item 6).
- **Item 8 (default UA)** — small change; holding until item 2 lands to avoid
  churn in the same files.
- **Item 9 (SQLite frontier)** — new module, non-trivial (aiosqlite schema,
  resume flag, checkpointer). Spec/design only.
- **Item 10 (rbloom dedup)** — trivial once item 9 lands.
- **Item 11 (delta crawl)** — requires item 9 state store.
- **Item 12 (sitemap-first)** — requires item 7.
- **Item 13 (graceful shutdown)** — requires item 9 checkpointer.
- **Item 14 (process pool)** — gated on py-spy profiling showing parse bottleneck
  after item 2 lands. Per plan, skip-and-document is an allowed outcome.
- **Item 15 (circuit breaker)** — requires item 9 TTL map.
- **Item 16 (OpenTelemetry)** — additive, low-risk; deferred purely for time.
- **Item 17 (Forma journal)** — small; deferred for time.

Baseline test count: 746 passing. This pass adds 23 new tests
(2 uvloop + 16 json_compat + 5 ratelimit). No regressions introduced.

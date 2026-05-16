"""Unit tests for p6 — mint -> replay orchestration (F1).

All browser/network is injected so the control loop is exercised
deterministically: mint_fn returns canned cookies, replay_fn returns
canned (status, headers, body) responses, sleep_fn records cool-downs.
"""

from __future__ import annotations

import json

import pytest

from flarecrawl.p6 import P6Config, run_p6

CLEAN = (200, {"content-type": "application/json"}, b'{"ok":true}')
AKAMAI = (200, {}, b"Powered and protected by Akamai")
CF_1020 = (403, {"server": "cloudflare"}, b"error code: 1020")


def _fresh_cookies(extra=None):
    # Far-future expiry so jarhealth says "fresh".
    base = [{"name": "_abck", "value": "v", "domain": ".x.com",
             "expires": 9_999_999_999}]
    if extra:
        base.extend(extra)
    return base


def _cfg(tmp_path, targets, **kw):
    kw.setdefault("base_cooldown", 1.0)
    return P6Config(
        mint_url="https://x.com/",
        jar_path=tmp_path / "jar.json",
        targets=targets,
        **kw,
    )


class TestHappyPath:
    def test_all_targets_ok(self, tmp_path):
        mints = []
        cfg = _cfg(tmp_path, ["https://x.com/a", "https://x.com/b"])
        res = run_p6(
            cfg,
            mint_fn=lambda *a: (mints.append(1), _fresh_cookies())[1],
            replay_fn=lambda *a: CLEAN,
            sleep_fn=lambda s: None,
        )
        assert res.targets_ok == 2
        assert res.targets_blocked == 0
        assert res.minted == 1  # one initial mint (no jar on disk)
        assert res.remints == 0
        assert res.terminal_abort is False

    def test_bodies_written(self, tmp_path):
        out = tmp_path / "out"
        cfg = _cfg(tmp_path, ["https://x.com/data.json"], output_dir=out)
        res = run_p6(
            cfg,
            mint_fn=lambda *a: _fresh_cookies(),
            replay_fn=lambda *a: CLEAN,
            sleep_fn=lambda s: None,
        )
        assert res.targets_ok == 1
        written = list(out.iterdir())
        assert len(written) == 1
        assert written[0].read_bytes() == b'{"ok":true}'

    def test_existing_fresh_jar_skips_initial_mint(self, tmp_path):
        jar = tmp_path / "jar.json"
        jar.write_text(json.dumps(_fresh_cookies()), encoding="utf-8")
        cfg = _cfg(tmp_path, ["https://x.com/a"])
        res = run_p6(
            cfg,
            mint_fn=lambda *a: pytest.fail("should not mint — jar is fresh"),
            replay_fn=lambda *a: CLEAN,
            sleep_fn=lambda s: None,
        )
        assert res.minted == 0
        assert res.targets_ok == 1


class TestBlockHandling:
    def test_block_triggers_remint_then_succeeds(self, tmp_path):
        calls = {"n": 0}

        def replay(*a):
            calls["n"] += 1
            return AKAMAI if calls["n"] == 1 else CLEAN

        cfg = _cfg(tmp_path, ["https://x.com/a"], max_remints=3)
        res = run_p6(
            cfg,
            mint_fn=lambda *a: _fresh_cookies(),
            replay_fn=replay,
            sleep_fn=lambda s: None,
        )
        assert res.targets_ok == 1
        assert res.remints == 1  # one re-mint after the block

    def test_block_budget_exhausted_is_cumulative_resume(self, tmp_path):
        cfg = _cfg(tmp_path, ["https://x.com/a", "https://x.com/b"],
                   max_remints=1)
        res = run_p6(
            cfg,
            mint_fn=lambda *a: _fresh_cookies(),
            replay_fn=lambda *a: AKAMAI,  # always blocked
            sleep_fn=lambda s: None,
        )
        # First target burns the single re-mint then is recorded blocked;
        # run continues (cumulative resume) rather than hammering.
        assert res.targets_blocked == 2
        assert res.remints == 1
        assert res.terminal_abort is False

    def test_terminal_block_aborts_run(self, tmp_path):
        cfg = _cfg(tmp_path, ["https://x.com/a", "https://x.com/b",
                              "https://x.com/c"])
        res = run_p6(
            cfg,
            mint_fn=lambda *a: _fresh_cookies(),
            replay_fn=lambda *a: CF_1020,
            sleep_fn=lambda s: None,
        )
        assert res.terminal_abort is True
        assert "cloudflare" in res.aborted_reason
        # First target blocked, the rest skipped (not retried).
        assert res.targets_blocked == 1
        assert res.targets_skipped == 2
        assert res.remints == 0  # no budget wasted on a terminal wall


class TestBackoff:
    def test_cooldown_grows_with_cumulative_remints(self, tmp_path):
        slept: list[float] = []
        cfg = _cfg(tmp_path, ["https://x.com/a"], max_remints=3,
                   base_cooldown=10.0, max_cooldown=1000.0)
        run_p6(
            cfg,
            mint_fn=lambda *a: _fresh_cookies(),
            replay_fn=lambda *a: AKAMAI,  # forces re-mints until budget out
            sleep_fn=slept.append,
        )
        # Cumulative exponential: each successive cool-down strictly grows
        # (10*2^0.., plus jitter in [0, base)).  Monotonic increasing lower
        # bounds guarantee separation.
        assert len(slept) == 3
        assert slept[0] < slept[1] < slept[2]


class TestResume:
    def test_resume_skips_journaled_targets(self, tmp_path):
        cfg = _cfg(tmp_path, ["https://x.com/a", "https://x.com/b"],
                   resume=True)
        # First run completes both.
        run_p6(cfg, mint_fn=lambda *a: _fresh_cookies(),
               replay_fn=lambda *a: CLEAN, sleep_fn=lambda s: None)
        # Second run with resume should skip both (journal has them).
        seen = []
        res = run_p6(
            cfg,
            mint_fn=lambda *a: _fresh_cookies(),
            replay_fn=lambda *a: (seen.append(1), CLEAN)[1],
            sleep_fn=lambda s: None,
        )
        assert res.targets_skipped == 2
        assert seen == []  # replay never called


class TestTransportError:
    def test_replay_exception_recorded_as_error(self, tmp_path):
        def boom(*a):
            raise ConnectionError("socket reset")

        cfg = _cfg(tmp_path, ["https://x.com/a"])
        res = run_p6(
            cfg,
            mint_fn=lambda *a: _fresh_cookies(),
            replay_fn=boom,
            sleep_fn=lambda s: None,
        )
        assert res.targets_failed == 1
        assert res.outcomes[0].status == "error"
        assert "socket reset" in (res.outcomes[0].error or "")


class TestSerialization:
    def test_result_json_serialisable(self, tmp_path):
        cfg = _cfg(tmp_path, ["https://x.com/a"])
        res = run_p6(cfg, mint_fn=lambda *a: _fresh_cookies(),
                     replay_fn=lambda *a: CLEAN, sleep_fn=lambda s: None)
        json.dumps(res.as_dict())  # must not raise


class TestEdgeCases:
    def test_same_basename_targets_no_clobber(self, tmp_path):
        # site/api/data and site/v2/api/data both basename "data".
        out = tmp_path / "out"
        cfg = _cfg(tmp_path, ["https://x.com/api/data",
                              "https://x.com/v2/api/data"], output_dir=out)
        bodies = iter([b"FIRST", b"SECOND"])
        res = run_p6(
            cfg,
            mint_fn=lambda *a: _fresh_cookies(),
            replay_fn=lambda *a: (200, {}, next(bodies)),
            sleep_fn=lambda s: None,
        )
        assert res.targets_ok == 2
        written = sorted(p.read_bytes() for p in out.iterdir())
        assert written == [b"FIRST", b"SECOND"]  # both survived, no clobber

    def test_chrome_devtools_jar_shape_on_disk(self, tmp_path):
        # Pre-existing jar in {"cookies":[...]} shape must not crash load.
        jar = tmp_path / "jar.json"
        jar.write_text(json.dumps({"cookies": _fresh_cookies()}),
                       encoding="utf-8")
        cfg = _cfg(tmp_path, ["https://x.com/a"])
        res = run_p6(
            cfg,
            mint_fn=lambda *a: pytest.fail("jar is fresh — must not mint"),
            replay_fn=lambda *a: CLEAN,
            sleep_fn=lambda s: None,
        )
        assert res.minted == 0
        assert res.targets_ok == 1

    def test_empty_mint_emits_event_and_is_bounded(self, tmp_path):
        events: list[str] = []
        cfg = _cfg(tmp_path, ["https://x.com/a"], max_remints=2)
        res = run_p6(
            cfg,
            mint_fn=lambda *a: [],            # mint always fails
            replay_fn=lambda *a: AKAMAI,      # blocked → triggers re-mint
            sleep_fn=lambda s: None,
            on_event=lambda e, p: events.append(e),
        )
        assert "mint_empty" in events
        # Bounded: initial mint + max_remints, then cumulative resume.
        assert res.minted <= 1 + cfg.max_remints
        assert res.targets_blocked == 1

    def test_corrupt_jar_falls_back_to_mint(self, tmp_path):
        jar = tmp_path / "jar.json"
        jar.write_text("{not valid json", encoding="utf-8")
        cfg = _cfg(tmp_path, ["https://x.com/a"])
        res = run_p6(
            cfg,
            mint_fn=lambda *a: _fresh_cookies(),
            replay_fn=lambda *a: CLEAN,
            sleep_fn=lambda s: None,
        )
        assert res.minted == 1  # corrupt jar → treated as empty → mint
        assert res.targets_ok == 1

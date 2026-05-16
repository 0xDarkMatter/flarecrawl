"""P6: mint -> replay anti-bot primitive for Flarecrawl — v0.29.0 (F1).

"P6" is the dance that carried an entire 9-connector hard-target harvest:

  1. MINT   — a headed/headless local Chromium navigates a URL on the
              target domain and lets the bot wall (Akamai / Cloudflare /
              Imperva) *deposit its cookie shells* (``_abck``, ``bm_*``,
              ``__cf_bm`` ...).  No JS-sensor solve is required — depositing
              the shells plus a real-Chrome TLS fingerprint is enough to
              clear the edge for non-locale paths.
  2. REPLAY — ``curl_cffi`` with ``--impersonate chrome131`` replays the
              real requests carrying the minted jar and a genuine Chrome
              JA3/JA4 handshake.

Hand-orchestrated it is undiscoverable and expert-only.  This module makes
it a primitive with the three things a real harvest needs and a one-off
script never has:

  * **Proactive jar freshness** — re-mint *before* a block when
    ``jarhealth`` says the shells are stale, not after burning a request.
  * **Cumulative exponential cool-down** — the Akamai egress-escalation
    trap: a tight re-mint-on-every-block loop keeps the *whole egress IP*
    flagged.  Backoff is keyed on the *total* re-mint count, not per
    target, so sustained pressure backs off globally.
  * **Terminal-block fast-fail** — a Cloudflare 1020 is keyed on the
    egress, not the session; minting cannot help.  Detected via
    ``blockdetect`` and aborts the run instead of wasting the budget.

Everything network/browser-facing is injected (``mint_fn`` / ``replay_fn``
/ ``now_fn`` / ``sleep_fn``) so the control loop is unit-testable without a
browser or a socket.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from .blockdetect import detect_block
from .jarhealth import inspect_jar

# (status, headers, body) — what a replay returns.
ReplayResponse = tuple[int, Mapping[str, str], bytes]
# (mint_url, headed, proxy) -> minted cookie list (Playwright shape).
MintFn = Callable[[str, bool, str | None], list[dict]]
# (url, cookies, impersonate, proxy) -> ReplayResponse.
ReplayFn = Callable[[str, list[dict], str, str | None], ReplayResponse]


@dataclass(slots=True)
class P6Config:
    mint_url: str
    jar_path: Path
    targets: list[str]
    impersonate: str = "chrome131"
    headed: bool = False
    max_remints: int = 3            # global cap across the whole run
    base_cooldown: float = 5.0      # seconds; grows 2**n with total re-mints
    max_cooldown: float = 300.0
    output_dir: Path | None = None  # write replay bodies here when set
    proxy: str | None = None
    expiring_threshold: float = 300.0
    resume: bool = False            # skip targets already in the journal


@dataclass(slots=True)
class TargetOutcome:
    url: str
    status: str                      # ok | blocked | error | skipped
    http_status: int | None = None
    block: dict | None = None        # BlockInfo.as_dict() when blocked
    path: str | None = None          # output file when written
    size: int | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class P6Result:
    mint_url: str
    jar_path: str
    minted: int                      # number of mint operations performed
    remints: int                     # re-mints (mints after the first)
    targets_total: int
    targets_ok: int
    targets_blocked: int
    targets_failed: int
    targets_skipped: int
    terminal_abort: bool
    aborted_reason: str
    outcomes: list[TargetOutcome] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["outcomes"] = [
            o.as_dict() if isinstance(o, TargetOutcome) else o
            for o in self.outcomes
        ]
        return d


def _journal_path(jar_path: Path) -> Path:
    return jar_path.parent / f".p6-journal-{jar_path.stem}.ndjson"


def _load_journal(jar_path: Path) -> set[str]:
    j = _journal_path(jar_path)
    if not j.exists():
        return set()
    done: set[str] = set()
    for line in j.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("status") == "ok" and rec.get("url"):
            done.add(rec["url"])
    return done


def _append_journal(jar_path: Path, outcome: TargetOutcome) -> None:
    with _journal_path(jar_path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(outcome)) + "\n")


def _default_mint_fn(mint_url: str, headed: bool, proxy: str | None) -> list[dict]:
    """Mint shells via a local Chromium + CDP navigation.

    Mirrors the recipe runner's local-browser branch.  Navigates to
    networkidle so the wall has time to deposit its cookies, then snapshots
    the jar.  No interaction / JS-sensor solve — shells + TLS are enough.
    """
    from .cdp import CDPClient
    from .config import get_account_id, get_api_token
    from .local_browser import LocalBrowser

    local_ctx = LocalBrowser(headless=not headed).__enter__()
    cdp_client = CDPClient(
        account_id=get_account_id() or "local",
        api_token=get_api_token() or "local",
    )
    try:
        page = cdp_client.new_page()
        try:
            page.apply_stealth()
        except Exception:
            pass
        page.navigate(mint_url, wait_until="networkidle0", timeout=60000)
        cookies = page.get_cookies()
        page.close()
        return cookies
    finally:
        try:
            cdp_client.close()
        except Exception:
            pass
        try:
            local_ctx.__exit__(None, None, None)
        except Exception:
            pass


def _default_replay_fn(
    url: str,
    cookies: list[dict],
    impersonate: str,
    proxy: str | None,
) -> ReplayResponse:
    """Replay one URL via curl_cffi carrying the minted jar + Chrome TLS."""
    from curl_cffi import requests as cffi_requests  # noqa: PLC0415

    proxies = {"http": proxy, "https": proxy} if proxy else None
    with cffi_requests.Session(impersonate=impersonate, timeout=60,
                               proxies=proxies) as s:
        if cookies:
            from .cookies import cookies_to_httpx
            s.cookies = {c.name: c.value for c in cookies_to_httpx(cookies).jar}
        r = s.get(url, allow_redirects=True)
        hdrs = {str(k): str(v) for k, v in dict(r.headers).items()}
        return r.status_code, hdrs, r.content


def _cooldown(remints: int, base: float, cap: float) -> float:
    """Cumulative exponential backoff with jitter, keyed on total re-mints."""
    raw = base * (2 ** remints)
    return min(raw, cap) + random.uniform(0, base)


def _write_body(output_dir: Path, url: str, body: bytes) -> tuple[str, int]:
    import hashlib
    from urllib.parse import urlparse
    output_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urlparse(url.split("?")[0]).path).name or "index"
    dest = output_dir / name
    # Two targets with the same basename (site/api/data and site/v2/api/data,
    # or differing query strings) must not clobber each other.  On collision,
    # suffix with a short stable hash of the full URL.
    if dest.exists():
        h = hashlib.sha256(url.encode()).hexdigest()[:8]
        stem, dot, ext = name.partition(".")
        name = f"{stem}.{h}{dot}{ext}" if dot else f"{name}.{h}"
        dest = output_dir / name
    dest.write_bytes(body)
    return str(dest), len(body)


def run_p6(
    cfg: P6Config,
    *,
    mint_fn: MintFn | None = None,
    replay_fn: ReplayFn | None = None,
    now_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_event: Callable[[str, dict], None] | None = None,
) -> P6Result:
    """Run the mint -> replay loop over ``cfg.targets``.

    Args:
        cfg: Run configuration.
        mint_fn / replay_fn: Injected for tests; default to a real local
            Chromium mint and a curl_cffi replay.
        now_fn / sleep_fn: Injected clock/sleep for deterministic tests.
        on_event: Optional ``(event, payload)`` hook for progress UI.

    Returns:
        ``P6Result`` with per-target outcomes.  ``terminal_abort=True``
        means a non-bypassable wall (CF 1020) ended the run early — the
        remaining targets are recorded as skipped, not retried.
    """
    mint_fn = mint_fn or _default_mint_fn
    replay_fn = replay_fn or _default_replay_fn

    def emit(event: str, **payload) -> None:
        if on_event:
            on_event(event, payload)

    done = _load_journal(cfg.jar_path) if cfg.resume else set()

    # Load any existing jar; mint if missing/expired/stale.  Accept both a
    # bare list and a Chrome DevTools export ({"cookies": [...]}).
    cookies: list[dict] = []
    if cfg.jar_path.exists():
        try:
            raw = json.loads(cfg.jar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = []
        if isinstance(raw, dict):
            raw = raw.get("cookies", [])
        cookies = [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []

    minted = 0
    remints = 0

    def do_mint(reason: str) -> None:
        nonlocal cookies, minted, remints
        if minted > 0:
            remints += 1
        emit("mint", reason=reason, n=minted + 1)
        cookies = mint_fn(cfg.mint_url, cfg.headed, cfg.proxy) or []
        minted += 1
        if not cookies:
            # Mint produced nothing — the wall didn't deposit shells (network
            # failure, wrong mint_url, or the page never settled).  Surface
            # it; the max_remints cap still bounds the wasted retries.
            emit("mint_empty", reason=reason, n=minted)
        try:
            cfg.jar_path.parent.mkdir(parents=True, exist_ok=True)
            cfg.jar_path.write_text(json.dumps(cookies, indent=2),
                                    encoding="utf-8")
        except OSError:
            pass

    health = inspect_jar(cookies, now=now_fn(),
                         expiring_threshold=cfg.expiring_threshold)
    if not health.ok:
        do_mint(f"initial jar {health.verdict}")

    outcomes: list[TargetOutcome] = []
    ok = blocked = failed = skipped = 0
    terminal_abort = False
    aborted_reason = ""

    targets = list(cfg.targets)
    for idx, url in enumerate(targets):
        if cfg.resume and url in done:
            outcomes.append(TargetOutcome(url, "skipped"))
            skipped += 1
            continue

        # Proactive freshness check between targets — re-mint *before* the
        # request if the shells went stale, rather than after a block.
        if minted > 0:
            health = inspect_jar(cookies, now=now_fn(),
                                 expiring_threshold=cfg.expiring_threshold)
            if not health.ok and remints < cfg.max_remints:
                cd = _cooldown(remints, cfg.base_cooldown, cfg.max_cooldown)
                emit("cooldown", seconds=round(cd, 1),
                     reason=f"proactive ({health.verdict})")
                sleep_fn(cd)
                do_mint(f"proactive {health.verdict}")

        attempt = 0
        while True:
            try:
                status, headers, body = replay_fn(
                    url, cookies, cfg.impersonate, cfg.proxy)
            except Exception as exc:  # transport failure
                outcome = TargetOutcome(url, "error", error=str(exc)[:300])
                failed += 1
                outcomes.append(outcome)
                _append_journal(cfg.jar_path, outcome)
                emit("target", url=url, status="error")
                break

            info = detect_block(status, headers, body)

            if info.terminal:
                # CF 1020 etc — non-bypassable, abort the whole run.
                terminal_abort = True
                aborted_reason = f"{info.vendor}:{info.kind}"
                outcome = TargetOutcome(url, "blocked", http_status=status,
                                        block=info.as_dict())
                blocked += 1
                outcomes.append(outcome)
                _append_journal(cfg.jar_path, outcome)
                emit("terminal", url=url, reason=aborted_reason)
                break

            if info.blocked:
                if remints < cfg.max_remints:
                    cd = _cooldown(remints, cfg.base_cooldown,
                                   cfg.max_cooldown)
                    emit("cooldown", seconds=round(cd, 1),
                         reason=f"block {info.vendor}:{info.kind}")
                    sleep_fn(cd)
                    do_mint(f"block {info.vendor}:{info.kind}")
                    attempt += 1
                    continue  # retry this target with the fresh jar
                # Budget exhausted — cumulative resume: record and move on
                # rather than keep hammering a flagged egress.
                outcome = TargetOutcome(url, "blocked", http_status=status,
                                        block=info.as_dict())
                blocked += 1
                outcomes.append(outcome)
                _append_journal(cfg.jar_path, outcome)
                emit("target", url=url, status="blocked")
                break

            # Clean response.
            path = size = None
            if cfg.output_dir is not None:
                try:
                    path, size = _write_body(cfg.output_dir, url, body)
                except OSError as exc:
                    outcome = TargetOutcome(url, "error",
                                            error=f"write failed: {exc}")
                    failed += 1
                    outcomes.append(outcome)
                    _append_journal(cfg.jar_path, outcome)
                    emit("target", url=url, status="error")
                    break
            outcome = TargetOutcome(url, "ok", http_status=status,
                                    path=path, size=size)
            ok += 1
            outcomes.append(outcome)
            _append_journal(cfg.jar_path, outcome)
            emit("target", url=url, status="ok")
            break

        if terminal_abort:
            for rest in targets[idx + 1:]:
                outcomes.append(TargetOutcome(rest, "skipped"))
                skipped += 1
            break

    return P6Result(
        mint_url=cfg.mint_url,
        jar_path=str(cfg.jar_path),
        minted=minted,
        remints=remints,
        targets_total=len(targets),
        targets_ok=ok,
        targets_blocked=blocked,
        targets_failed=failed,
        targets_skipped=skipped,
        terminal_abort=terminal_abort,
        aborted_reason=aborted_reason,
        outcomes=outcomes,
    )

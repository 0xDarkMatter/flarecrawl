"""Synthetic interaction routines for headless browsers — v0.26.0 P1.

Behavioural-fingerprint engines (Akamai BMP, DataDome, PerimeterX) check
for *interaction history* before blessing a session: mouse movement, a
small scroll or two, idle time. Headless Chromium has none of that, even
with ``stealth_init.js`` patches applied.

This module synthesises a believable interaction history before any
meaningful page action. Cost: ~1.0–1.5 s per page. Run once on
navigation, then real scraping proceeds normally.

Hypothesis (UPGRADE-PLAN-v0.26.0.md, H1): if the gate is behavioural,
adding mouse moves + scrolls + idle gaps before the first JS-detectable
action is enough to pass.
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .cdp import SyncCDPPage


def _bezier_path(x0: float, y0: float, x1: float, y1: float, steps: int) -> list[tuple[float, float]]:
    """Cubic-Bezier path from (x0,y0) to (x1,y1) with two random control points.

    Returns a list of intermediate points sampled uniformly along the
    parameter t. Real human cursor motion is approximated well by cubic
    Beziers with control points perturbed off the straight line by
    20-40% of the path length perpendicular to it.
    """
    # Perpendicular unit vector
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    perp_x, perp_y = -dy / length, dx / length
    # Two control points along the line at 1/3 and 2/3, perturbed sideways
    deflection = length * random.uniform(0.15, 0.35) * random.choice([-1, 1])
    cx1 = x0 + dx / 3 + perp_x * deflection
    cy1 = y0 + dy / 3 + perp_y * deflection
    cx2 = x0 + 2 * dx / 3 + perp_x * deflection * 0.6
    cy2 = y0 + 2 * dy / 3 + perp_y * deflection * 0.6
    points = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        # Cubic Bezier formula
        x = u**3 * x0 + 3 * u**2 * t * cx1 + 3 * u * t**2 * cx2 + t**3 * x1
        y = u**3 * y0 + 3 * u**2 * t * cy1 + 3 * u * t**2 * cy2 + t**3 * y1
        points.append((x, y))
    return points


def humanize_page(
    page: "SyncCDPPage",
    *,
    viewport: tuple[int, int] = (1440, 900),
    profile: str = "fast",
    seed: int | None = None,
) -> dict:
    """Inject synthetic mouse/scroll/idle interactions before page-level work.

    Args:
        page: Connected SyncCDPPage (must have a navigated document).
        viewport: (width, height) bounds. Mouse moves stay inside.
        profile: "fast" (~700ms total), "natural" (~1500ms), "thorough" (~3000ms).
        seed: Deterministic RNG seed for tests; ``None`` randomises.

    Returns:
        dict with telemetry: ``moves``, ``scrolls``, ``elapsed_ms``.
    """
    import time

    rng = random.Random(seed) if seed is not None else random
    width, height = viewport

    profiles = {
        "fast":     {"moves": 1, "scrolls": 1, "idle_ms_each": (60, 120)},
        "natural":  {"moves": 2, "scrolls": 2, "idle_ms_each": (120, 280)},
        "thorough": {"moves": 4, "scrolls": 3, "idle_ms_each": (200, 500)},
    }
    cfg = profiles.get(profile, profiles["natural"])

    started = time.time()
    n_moves = int(cfg["moves"])
    n_scrolls = int(cfg["scrolls"])
    idle_lo, idle_hi = cfg["idle_ms_each"]  # type: ignore[misc]

    # ── Mouse moves ──────────────────────────────────────────────────────
    cur_x, cur_y = (
        rng.uniform(width * 0.1, width * 0.4),
        rng.uniform(height * 0.1, height * 0.4),
    )
    for _ in range(n_moves):
        target_x = rng.uniform(width * 0.2, width * 0.8)
        target_y = rng.uniform(height * 0.2, height * 0.8)
        steps = rng.randint(10, 25)
        for px, py in _bezier_path(cur_x, cur_y, target_x, target_y, steps):
            try:
                page.send("Input.dispatchMouseEvent", {
                    "type": "mouseMoved",
                    "x": int(px),
                    "y": int(py),
                    "button": "none",
                })
            except Exception:
                # Don't fail navigation over a missed mouse event
                break
            time.sleep(rng.uniform(0.005, 0.020))
        cur_x, cur_y = target_x, target_y
        time.sleep(rng.uniform(idle_lo / 1000, idle_hi / 1000))

    # ── Scrolls ──────────────────────────────────────────────────────────
    for _ in range(n_scrolls):
        # Small, plausible scroll deltas (40-200 px), occasionally negative
        delta_y = rng.uniform(40, 200) * rng.choice([1, 1, 1, -1])
        try:
            page.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel",
                "x": int(cur_x),
                "y": int(cur_y),
                "deltaX": 0,
                "deltaY": delta_y,
            })
        except Exception:
            break
        time.sleep(rng.uniform(idle_lo / 1000, idle_hi / 1000))

    # ── Final idle so any IntersectionObserver callbacks settle ──────────
    time.sleep(rng.uniform(0.10, 0.25))

    elapsed_ms = int((time.time() - started) * 1000)
    return {
        "moves": n_moves,
        "scrolls": n_scrolls,
        "profile": profile,
        "elapsed_ms": elapsed_ms,
    }

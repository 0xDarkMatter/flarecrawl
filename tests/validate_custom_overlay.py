"""Validate every custom-overlay fingerprint by building synthetic
fixtures from its patterns and asserting WappalyzerClient.analyze()
fires the tech.

Not a pytest target - run directly:

    uv run python tests/validate_custom_overlay.py
    uv run python tests/validate_custom_overlay.py --json > out.json

For each top-level tech in custom_fingerprints.json (excluding _meta /
_disabled / _added_meta / _disabled_meta), we:

1. Read all of its scriptSrc / html / cookies / meta / js / dom patterns.
2. Synthesise the smallest plausible fixture that should match each one.
3. Call WappalyzerClient.analyze() with that fixture.
4. Report which patterns fire and which don't.

A "fingerprint passes" if AT LEAST ONE of its declared patterns fires
synthetically - that's the minimum bar for "this overlay entry can
detect anything". Patterns that don't fire individually are listed so
we can fix or annotate them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flarecrawl.wappalyzer import WappalyzerClient  # noqa: E402

OVERLAY_PATH = (
    ROOT / "src" / "flarecrawl" / "wappalyzer_data" / "custom_fingerprints.json"
)

NON_TECH_KEYS = {"_meta", "_disabled", "_disabled_meta", "_added_meta"}


def _strip_pattern_meta(pattern: str) -> str:
    """Wappalyzer patterns use ``\;version:\1\;confidence:50`` suffixes.

    Strip the meta so we can use the regex part to build a synthetic.
    """
    return pattern.split("\\;")[0]


def _pick_class_member(cls: str) -> str:
    """Given the contents of a char class like ``a-z0-9-`` or ``^"'``,
    return a single character that satisfies it."""
    negated = cls.startswith("^")
    if negated:
        excluded = set(cls[1:])
        for cand in "abcdefghijklmnopqrstuvwxyz0123456789-_ /:.":
            if cand not in excluded:
                return cand
        return "a"
    if "a-z" in cls or "A-Z" in cls:
        return "a"
    if "0-9" in cls or "\\d" in cls:
        return "1"
    for cand in cls:
        if cand not in "\\^":
            return cand
    return "a"


def _synthesise_for_regex(pat: str) -> str:
    """Best-effort: produce a literal string that matches the regex.

    Not a general inverse - just good enough for our hand-written
    patterns. Drops anchors, picks first alternative, picks a single
    member from each character class.
    """
    s = _strip_pattern_meta(pat)
    # Optional groups -> drop
    while True:
        m = re.search(r"\(\?:([^()]+)\)\?", s)
        if not m:
            break
        s = s[: m.start()] + s[m.end() :]
    while True:
        m = re.search(r"\(([^()]+)\)\?", s)
        if not m:
            break
        s = s[: m.start()] + s[m.end() :]
    # Non-capturing group -> first alternative
    while True:
        m = re.search(r"\(\?:([^()]+)\)", s)
        if not m:
            break
        first = m.group(1).split("|")[0]
        s = s[: m.start()] + first + s[m.end() :]
    # Capturing group -> first alternative
    while True:
        m = re.search(r"\(([^()]+)\)", s)
        if not m:
            break
        first = m.group(1).split("|")[0]
        s = s[: m.start()] + first + s[m.end() :]

    def _sub_class(m: re.Match) -> str:
        body = m.group(1)
        quant = m.group(2)
        ch = _pick_class_member(body)
        if quant == "*":
            return ""
        return ch

    s = re.sub(r"\[([^\]]+)\](\{[^}]+\}|[+*?]|)", _sub_class, s)
    s = re.sub(r"\\d\+", "1", s)
    s = re.sub(r"\\d\*", "", s)
    s = re.sub(r"\\d", "1", s)
    s = re.sub(r"\\w\+", "abc", s)
    s = re.sub(r"\\w\*", "", s)
    s = re.sub(r"\\w", "a", s)
    s = re.sub(r"\.\+\?", "x", s)
    s = re.sub(r"\.\*\?", "", s)
    s = re.sub(r"\.\+", "x", s)
    s = re.sub(r"\.\*", "", s)
    s = s.replace("\\b", "")
    s = s.replace("^", "").replace("$", "")
    s = re.sub(r"\\(.)", r"\1", s)
    return s


def _eval_fingerprint(client: WappalyzerClient, name: str, tech: dict) -> dict:
    """Try each pattern individually and return a per-signal report."""
    report = {
        "name": name,
        "has_overlay_flag": tech.get("_overlay") is True,
        "signals": {},
        "any_fires": False,
        "all_fires": True,
        "failed_signals": [],
    }

    def run(html="", headers=None, cookies=None, meta=None, script_src=None,
            js_globals=None):
        detections = client.analyze(
            html=html or "",
            headers=headers,
            cookies=cookies,
            meta=meta,
            script_src=script_src,
            js_globals=js_globals,
        )
        return name in {d.name for d in detections}

    # scriptSrc
    for pat in tech.get("scriptSrc", []) or []:
        synth = _synthesise_for_regex(pat)
        fires = run(script_src=[synth])
        report["signals"][f"scriptSrc::{pat}"] = {"fires": fires, "synth": synth}

    # html
    for pat in tech.get("html", []) or []:
        synth = _synthesise_for_regex(pat)
        # Embed inside a minimal page; the html matcher scans raw HTML
        html_doc = f"<!doctype html><html><body>{synth}</body></html>"
        fires = run(html=html_doc)
        report["signals"][f"html::{pat}"] = {"fires": fires, "synth": synth}

    # cookies
    for name_, pat in (tech.get("cookies") or {}).items():
        synth_val = _synthesise_for_regex(pat) if pat else "1"
        fires = run(cookies={name_: synth_val or "1"})
        report["signals"][f"cookies::{name_}"] = {"fires": fires}

    # meta
    for name_, pat in (tech.get("meta") or {}).items():
        synth_val = _synthesise_for_regex(pat) if pat else "1"
        # The analyze() impl pre-extracts meta from HTML if meta=None,
        # but we pass meta= directly to avoid that path.
        fires = run(meta={name_: synth_val or "1"})
        report["signals"][f"meta::{name_}"] = {"fires": fires}

    # headers
    for header_name, pat in (tech.get("headers") or {}).items():
        synth_val = _synthesise_for_regex(pat) if pat else "x"
        fires = run(headers={header_name: synth_val or "x"})
        report["signals"][f"headers::{header_name}"] = {"fires": fires}

    # js globals
    for js_path, pat in (tech.get("js") or {}).items():
        synth_val = _synthesise_for_regex(pat) if pat else "1"
        fires = run(js_globals={js_path: synth_val or "1"})
        report["signals"][f"js::{js_path}"] = {"fires": fires}

    # dom selectors - synthesise minimal matching HTML
    for sel in tech.get("dom", []) or []:
        if not isinstance(sel, str):
            continue
        synth_html = _dom_synth(sel)
        if synth_html:
            html_doc = f"<!doctype html><html><body>{synth_html}</body></html>"
            fires = run(html=html_doc)
            report["signals"][f"dom::{sel}"] = {"fires": fires, "synth": synth_html}

    # Roll up
    fires_per_signal = [v["fires"] for v in report["signals"].values()]
    report["any_fires"] = any(fires_per_signal) if fires_per_signal else False
    report["all_fires"] = all(fires_per_signal) if fires_per_signal else True
    report["failed_signals"] = [
        sig for sig, v in report["signals"].items() if not v["fires"]
    ]
    return report


def _dom_synth(selector: str) -> str:
    """Build minimal HTML matching common DOM-selector shapes.

    Supports:
        tag[attr*='value']
        tag[attr='value']
        tag.classname
        tag#id
        tag (alone)
    """
    s = selector.strip()
    m = re.match(r"^(\w+)\[(\w+)\*?=['\"]([^'\"]+)['\"]\]$", s)
    if m:
        tag, attr, val = m.group(1), m.group(2), m.group(3)
        # Make sure the value is realistic (e.g. https://...)
        if attr in ("href", "src", "action"):
            if "://" not in val and not val.startswith("/"):
                val = f"https://{val}/"
        return f'<{tag} {attr}="{val}"></{tag}>'
    m = re.match(r"^(\w+)\[(\w+)\]$", s)
    if m:
        tag, attr = m.group(1), m.group(2)
        return f'<{tag} {attr}="x"></{tag}>'
    m = re.match(r"^(\w+)\.([\w-]+)$", s)
    if m:
        return f'<{m.group(1)} class="{m.group(2)}"></{m.group(1)}>'
    m = re.match(r"^(\w+)#([\w-]+)$", s)
    if m:
        return f'<{m.group(1)} id="{m.group(2)}"></{m.group(1)}>'
    m = re.match(r"^(\w+)$", s)
    if m:
        return f"<{m.group(1)}></{m.group(1)}>"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument("--only", action="append", default=[],
                        help="restrict to specific tech name(s)")
    args = parser.parse_args()

    overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    client = WappalyzerClient()
    client._load()

    results = []
    for tech_name, body in overlay.items():
        if tech_name in NON_TECH_KEYS:
            continue
        if not isinstance(body, dict):
            continue
        # Skip pure implies-only entries (no detection patterns).
        has_detect = any(k in body for k in
                         ("scriptSrc", "html", "cookies", "meta",
                          "headers", "js", "dom"))
        if not has_detect:
            results.append({
                "name": tech_name,
                "kind": "implies-only",
                "implies": body.get("implies", []),
                "any_fires": True,  # not testable, mark passthrough
                "failed_signals": [],
            })
            continue
        if args.only and tech_name not in args.only:
            continue
        results.append(_eval_fingerprint(client, tech_name, body))

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    # Pretty summary
    passing = [r for r in results if r.get("any_fires")]
    failing = [r for r in results if not r.get("any_fires")]
    partial = [r for r in results
               if r.get("any_fires") and r.get("failed_signals")]

    print(f"Custom-overlay fingerprints inventoried: {len(results)}")
    print(f"  All-signals fire   : "
          f"{sum(1 for r in results if not r.get('failed_signals'))}")
    print(f"  Some-signals fire  : {len(partial)}")
    print(f"  No signals fire    : {len(failing)}")
    print()
    if failing:
        print("FAIL (no synthetic signal fires):")
        for r in failing:
            print(f"  - {r['name']}")
            for sig in r["failed_signals"]:
                print(f"      X  {sig}")
        print()
    if partial:
        print("PARTIAL (at least one signal fires, others don't):")
        for r in partial:
            print(f"  - {r['name']}")
            for sig in r["failed_signals"]:
                print(f"      X  {sig}")
    return 0 if not failing else 1


if __name__ == "__main__":
    sys.exit(main())

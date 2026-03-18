"""Benchmark: Firecrawl vs Flarecrawl comparison.

Runs both CLIs against a set of test URLs and compares speed, content accuracy,
cost, reliability, JS rendering, and output quality.

Usage:
    python tests/bench.py                        # Full benchmark (3 runs each)
    python tests/bench.py --runs 1               # Quick single run
    python tests/bench.py --urls-only            # Just list test URLs
    python tests/bench.py --tool flarecrawl      # Single tool only
    python tests/bench.py --output results.json  # Save raw results
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean, stdev

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TEST_URLS = [
    {
        "url": "https://httpbin.org",
        "category": "api-docs",
        "known_text": "A simple HTTP Request & Response Service",
        "js_text": "HTTP Methods",
        "expected_headings": 2,
        "expected_links_min": 5,
    },
    {
        "url": "https://docs.python.org/3/library/json.html",
        "category": "documentation",
        "known_text": "JSON encoder and decoder",
        "js_text": None,
        "expected_headings": 5,
        "expected_links_min": 10,
    },
    {
        "url": "https://news.ycombinator.com",
        "category": "dynamic",
        "known_text": "Hacker News",
        "js_text": None,
        "expected_headings": 0,
        "expected_links_min": 30,
    },
    {
        "url": "https://en.wikipedia.org/wiki/Web_scraping",
        "category": "long-content",
        "known_text": "Web scraping",
        "js_text": None,
        "expected_headings": 10,
        "expected_links_min": 50,
    },
    {
        "url": "https://blog.cloudflare.com/browser-rendering-open-api/",
        "category": "blog-article",
        "known_text": "browser-rendering",
        "js_text": None,
        "expected_headings": 3,
        "expected_links_min": 5,
    },
    {
        "url": "https://react.dev",
        "category": "spa",
        "known_text": "React",
        "js_text": None,
        "expected_headings": 2,
        "expected_links_min": 10,
    },
    {
        "url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript",
        "category": "heavy-docs",
        "known_text": "JavaScript",
        "js_text": None,
        "expected_headings": 5,
        "expected_links_min": 20,
    },
    {
        "url": "https://github.com/trending",
        "category": "dynamic-heavy",
        "known_text": "Trending",
        "js_text": None,
        "expected_headings": 0,
        "expected_links_min": 20,
    },
]

# Pricing models
# Firecrawl: Scale plan $99/month for 100K credits (1 credit per scrape)
FIRECRAWL_CREDIT_COST = 1.0
FIRECRAWL_FREE_CREDITS = 500
FIRECRAWL_PAID_CREDITS = 100_000
FIRECRAWL_PAID_PRICE = 99.0  # $/month (Scale plan)
FIRECRAWL_COST_PER_CREDIT = FIRECRAWL_PAID_PRICE / FIRECRAWL_PAID_CREDITS

# Flarecrawl: $5/month Workers Paid plan + browser rendering at $0.09/hr
FLARECRAWL_FREE_MS = 600_000  # 10 min/day
FLARECRAWL_WORKERS_BASE = 5.0  # $/month Workers Paid plan
FLARECRAWL_PAID_RATE = 0.09 / 3_600_000  # $0.09/hr in $/ms


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ScrapeResult:
    tool: str
    url: str
    run: int
    elapsed_s: float = 0.0
    exit_code: int = -1
    content: str = ""
    content_length: int = 0
    raw_json: dict = field(default_factory=dict)
    error: str = ""
    success: bool = False
    heading_count: int = 0
    link_count: int = 0
    known_text_found: bool = False
    js_text_found: bool = False
    browser_ms: int = 0
    credits_used: int = 0
    valid_json: bool = False
    has_envelope: bool = False
    metadata_fields: int = 0


@dataclass
class URLSummary:
    url: str
    category: str
    tool: str
    runs: int = 0
    successes: int = 0
    success_rate: float = 0.0
    avg_elapsed_s: float = 0.0
    min_elapsed_s: float = 0.0
    max_elapsed_s: float = 0.0
    stddev_elapsed_s: float = 0.0
    avg_content_length: int = 0
    content_length_stddev: float = 0.0
    avg_headings: float = 0.0
    avg_links: float = 0.0
    known_text_rate: float = 0.0
    js_text_rate: float = 0.0
    avg_browser_ms: float = 0.0
    avg_credits: float = 0.0
    cost_per_page: float = 0.0
    valid_json_rate: float = 0.0
    envelope_rate: float = 0.0
    avg_metadata_fields: float = 0.0


# ---------------------------------------------------------------------------
# CLI runners
# ---------------------------------------------------------------------------


def run_firecrawl(url: str, timeout: int = 60) -> dict:
    """Run firecrawl scrape and return parsed result.

    firecrawl CLI v1.9+ outputs raw markdown to stdout by default.
    --timing adds a JSON timing block as a prefix to stderr.
    --json is a FORMAT flag (AI extraction), NOT an output-mode flag.
    """
    env = os.environ.copy()
    if "FIRECRAWL_API_KEY" not in env:
        return {"error": "FIRECRAWL_API_KEY not set", "exit_code": 2}

    # Resolve the Node.js firecrawl CLI (avoid Python SDK conflict on PATH)
    firecrawl_cmd = os.environ.get("FIRECRAWL_CMD", "firecrawl")

    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [firecrawl_cmd, "scrape", url, "--timing"],
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        elapsed = time.perf_counter() - start
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "exit_code": -1, "elapsed": timeout}
    # Decode as UTF-8 (firecrawl outputs markdown with unicode)
    proc.stdout = proc.stdout.decode("utf-8", errors="replace") if isinstance(proc.stdout, bytes) else proc.stdout
    proc.stderr = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else proc.stderr

    result = {
        "exit_code": proc.returncode,
        "elapsed": elapsed,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }

    # firecrawl --timing: markdown on stdout, timing JSON on stderr
    content = (proc.stdout or "").strip()
    result["content"] = content
    result["data"] = {"markdown": content}
    result["metadata"] = {}
    result["credits_used"] = 1  # 1 credit per scrape

    # Parse timing from stderr (format: "Timing: { ... }")
    stderr = proc.stderr or ""
    timing_match = re.search(r"Timing:\s*(\{.*\})", stderr, re.DOTALL)
    if timing_match:
        try:
            timing_data = json.loads(timing_match.group(1))
            result["metadata"]["timing"] = timing_data
            duration_str = timing_data.get("duration", "")
            m = re.search(r"(\d+)ms", duration_str)
            if m:
                result["api_duration_ms"] = int(m.group(1))
        except json.JSONDecodeError:
            pass

    if not content and proc.returncode != 0:
        result["error"] = stderr.strip() or f"exit code {proc.returncode}"

    return result


def run_flarecrawl(url: str, timeout: int = 60, js_mode: bool = False) -> dict:
    """Run flarecrawl scrape and return parsed result."""
    cmd = ["flarecrawl", "scrape", url, "--json", "--timing", "--no-cache"]
    if js_mode:
        cmd.append("--js")
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - start
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "exit_code": -1, "elapsed": timeout}
    proc.stdout = proc.stdout.decode("utf-8", errors="replace") if isinstance(proc.stdout, bytes) else proc.stdout
    proc.stderr = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else proc.stderr

    result = {
        "exit_code": proc.returncode,
        "elapsed": elapsed,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }

    stdout = proc.stdout.strip()
    if stdout:
        try:
            data = json.loads(stdout)
            result["data"] = data
            content = data.get("data", {})
            if isinstance(content, dict):
                result["content"] = content.get("content", "")
            result["envelope"] = "data" in data and "meta" in data
        except json.JSONDecodeError:
            result["error"] = "json_parse_error"

    # Extract browser time from stderr
    stderr = proc.stderr or ""
    m = re.search(r"Browser time:\s*(\d+)ms", stderr)
    if m:
        result["browser_ms"] = int(m.group(1))

    return result


# ---------------------------------------------------------------------------
# Content analysis
# ---------------------------------------------------------------------------


def analyze_content(content: str, url_config: dict) -> dict:
    if not content:
        return {
            "heading_count": 0,
            "link_count": 0,
            "known_text_found": False,
            "js_text_found": False,
        }

    headings = len(re.findall(r"^#{1,6}\s+", content, re.MULTILINE))
    links = len(re.findall(r"\[.*?\]\(.*?\)", content))

    known = url_config.get("known_text", "")
    known_found = known.lower() in content.lower() if known else True

    js_text = url_config.get("js_text")
    js_found = js_text.lower() in content.lower() if js_text else True

    return {
        "heading_count": headings,
        "link_count": links,
        "known_text_found": known_found,
        "js_text_found": js_found,
    }


def content_similarity(content_a: str, content_b: str) -> float:
    if not content_a or not content_b:
        return 0.0
    return difflib.SequenceMatcher(None, content_a, content_b).ratio()


# ---------------------------------------------------------------------------
# Single scrape
# ---------------------------------------------------------------------------


def run_single(tool: str, url_config: dict, run_num: int) -> ScrapeResult:
    url = url_config["url"]
    result = ScrapeResult(tool=tool, url=url, run=run_num)

    if tool == "firecrawl":
        raw = run_firecrawl(url)
    else:
        # Use --js mode for URLs that define js_text (need JS rendering)
        needs_js = url_config.get("js_text") is not None
        raw = run_flarecrawl(url, js_mode=needs_js)

    result.exit_code = raw.get("exit_code", -1)
    result.elapsed_s = raw.get("elapsed", 0.0)
    result.error = raw.get("error", "")

    content = raw.get("content", "")
    result.content = content
    result.content_length = len(content)
    # Success = got content (firecrawl outputs markdown, not JSON)
    result.success = result.exit_code == 0 and not result.error and len(content) > 0

    result.raw_json = raw.get("data", {})
    result.valid_json = "data" in raw

    if tool == "firecrawl":
        result.has_envelope = False  # firecrawl outputs raw markdown
        meta = raw.get("metadata", {})
        result.metadata_fields = len(meta) if isinstance(meta, dict) else 0
        result.credits_used = raw.get("credits_used", 1)
    else:
        result.has_envelope = raw.get("envelope", False)
        data = raw.get("data", {})
        if isinstance(data, dict):
            meta = data.get("meta", {})
            result.metadata_fields = len(meta) if isinstance(meta, dict) else 0
        result.browser_ms = raw.get("browser_ms", 0)

    analysis = analyze_content(content, url_config)
    result.heading_count = analysis["heading_count"]
    result.link_count = analysis["link_count"]
    result.known_text_found = analysis["known_text_found"]
    result.js_text_found = analysis["js_text_found"]

    return result


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate(results: list[ScrapeResult], url_config: dict, tool: str) -> URLSummary:
    summary = URLSummary(url=url_config["url"], category=url_config["category"], tool=tool)
    summary.runs = len(results)
    successes = [r for r in results if r.success]
    summary.successes = len(successes)
    summary.success_rate = len(successes) / len(results) if results else 0.0

    if not successes:
        return summary

    times = [r.elapsed_s for r in successes]
    summary.avg_elapsed_s = round(mean(times), 2)
    summary.min_elapsed_s = round(min(times), 2)
    summary.max_elapsed_s = round(max(times), 2)
    summary.stddev_elapsed_s = round(stdev(times), 2) if len(times) > 1 else 0.0

    lengths = [r.content_length for r in successes]
    summary.avg_content_length = int(mean(lengths))
    summary.content_length_stddev = round(stdev(lengths), 1) if len(lengths) > 1 else 0.0

    summary.avg_headings = round(mean(r.heading_count for r in successes), 1)
    summary.avg_links = round(mean(r.link_count for r in successes), 1)
    summary.known_text_rate = sum(r.known_text_found for r in successes) / len(successes)
    summary.js_text_rate = sum(r.js_text_found for r in successes) / len(successes)
    summary.valid_json_rate = sum(r.valid_json for r in successes) / len(successes)
    summary.envelope_rate = sum(r.has_envelope for r in successes) / len(successes)
    summary.avg_metadata_fields = round(mean(r.metadata_fields for r in successes), 1)

    if tool == "firecrawl":
        summary.avg_credits = round(mean(r.credits_used for r in successes), 1)
        summary.cost_per_page = round(FIRECRAWL_COST_PER_CREDIT, 6)
    else:
        summary.avg_browser_ms = round(mean(r.browser_ms for r in successes), 0)
        summary.cost_per_page = round(summary.avg_browser_ms * FLARECRAWL_PAID_RATE, 6)

    return summary


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

WEIGHTS = {
    "speed": 0.20,
    "content_accuracy": 0.25,
    "cost": 0.20,
    "reliability": 0.15,
    "js_rendering": 0.10,
    "output_quality": 0.10,
}


def score_tool(summaries: list[URLSummary], other_summaries: list[URLSummary]) -> dict:
    scores = {}
    successful = [s for s in summaries if s.successes > 0]

    if not successful:
        return {k: 1 for k in WEIGHTS} | {"weighted_total": 1.0}

    # Speed (lower = better)
    avg_time = mean(s.avg_elapsed_s for s in successful)
    if avg_time <= 3:
        scores["speed"] = 5
    elif avg_time <= 6:
        scores["speed"] = 4
    elif avg_time <= 12:
        scores["speed"] = 3
    elif avg_time <= 20:
        scores["speed"] = 2
    else:
        scores["speed"] = 1

    # Content accuracy
    known_rate = mean(s.known_text_rate for s in successful)
    avg_headings = mean(s.avg_headings for s in successful)
    avg_links = mean(s.avg_links for s in successful)
    content_score = known_rate * 3 + min(avg_headings / 10, 1) + min(avg_links / 30, 1)
    scores["content_accuracy"] = min(5, max(1, round(content_score)))

    # Cost (lower = better)
    avg_cost = mean(s.cost_per_page for s in successful)
    if avg_cost <= 0.0001:
        scores["cost"] = 5
    elif avg_cost <= 0.0005:
        scores["cost"] = 4
    elif avg_cost <= 0.001:
        scores["cost"] = 3
    elif avg_cost <= 0.005:
        scores["cost"] = 2
    else:
        scores["cost"] = 1

    # Reliability
    avg_success = mean(s.success_rate for s in summaries)
    scores["reliability"] = min(5, max(1, round(avg_success * 5)))

    # JS rendering
    js_rate = mean(s.js_text_rate for s in successful)
    scores["js_rendering"] = min(5, max(1, round(js_rate * 5)))

    # Output quality
    json_rate = mean(s.valid_json_rate for s in successful)
    envelope_rate = mean(s.envelope_rate for s in successful)
    meta_richness = min(1.0, mean(s.avg_metadata_fields for s in successful) / 10)
    oq = (json_rate + envelope_rate + meta_richness) / 3 * 5
    scores["output_quality"] = min(5, max(1, round(oq)))

    scores["weighted_total"] = round(sum(scores[k] * WEIGHTS[k] for k in WEIGHTS), 2)

    return scores


# ---------------------------------------------------------------------------
# Display (ASCII-safe for Windows)
# ---------------------------------------------------------------------------


def p(text: str = "") -> None:
    """Print with UTF-8 encoding, replacing unencodable chars."""
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def print_header(text: str) -> None:
    p()
    p("=" * 70)
    p(f"  {text}")
    p("=" * 70)


def print_section(text: str) -> None:
    p()
    p(f"--- {text} ---")


def print_progress(tool: str, url: str, run: int, total_runs: int, result: ScrapeResult) -> None:
    status = "OK  " if result.success else "FAIL"
    p(
        f"  [{status}] {tool:11s} | {url:55s} | "
        f"run {run}/{total_runs} | {result.elapsed_s:.1f}s | "
        f"{result.content_length:,} chars"
    )


def print_comparison_table(fc_summaries: list[URLSummary], fl_summaries: list[URLSummary]) -> None:
    print_section("Per-URL Comparison")

    p(
        f"  {'URL':<40s} {'Tool':<12s} {'Avg Time':>9s} {'Content':>9s} "
        f"{'Headings':>9s} {'Links':>7s} {'Known':>6s} {'JS':>4s} {'Cost/pg':>10s}"
    )
    p(f"  {'-' * 110}")

    for fc, fl in zip(fc_summaries, fl_summaries):
        short_url = fc.url.replace("https://", "")[:38]
        for s in [fc, fl]:
            known = "Y" if s.known_text_rate == 1.0 else "N"
            js = "Y" if s.js_text_rate == 1.0 else "N"
            cost = f"${s.cost_per_page:.5f}" if s.cost_per_page > 0 else "free tier"
            label = short_url if s == fc else ""
            p(
                f"  {label:<40s} {s.tool:<12s} {s.avg_elapsed_s:>8.1f}s "
                f"{s.avg_content_length:>8,d} {s.avg_headings:>9.0f} "
                f"{s.avg_links:>7.0f} {known:>6s} {js:>4s} {cost:>10s}"
            )
        p()


def print_similarity(similarities: list[dict]) -> None:
    print_section("Content Similarity (firecrawl vs flarecrawl)")
    for s in similarities:
        short_url = s["url"].replace("https://", "")[:50]
        ratio = s["similarity"]
        bar_len = int(ratio * 30)
        bar = "#" * bar_len + "." * (30 - bar_len)
        p(f"  {short_url:<52s} [{bar}] {ratio:.0%}")


def print_scores(fc_scores: dict, fl_scores: dict) -> None:
    print_section("Final Scores (1-5 scale)")

    p(f"  {'Dimension':<22s} {'Weight':>7s} {'Firecrawl':>10s} {'Flarecrawl':>11s}")
    p(f"  {'-' * 55}")

    for dim in WEIGHTS:
        w = f"{WEIGHTS[dim]:.0%}"
        fc_val = fc_scores[dim]
        fl_val = fl_scores[dim]
        fc_marker = " *" if fc_val > fl_val else "  " if fc_val == fl_val else "  "
        fl_marker = " *" if fl_val > fc_val else "  " if fl_val == fc_val else "  "
        p(
            f"  {dim:<22s} {w:>7s} "
            f"{fc_val:>8d}{fc_marker} "
            f"{fl_val:>9d}{fl_marker}"
        )

    p(f"  {'-' * 55}")
    fc_total = fc_scores["weighted_total"]
    fl_total = fl_scores["weighted_total"]
    fc_win = " <=" if fc_total >= fl_total else "   "
    fl_win = " <=" if fl_total >= fc_total else "   "
    p(
        f"  {'WEIGHTED TOTAL':<22s} {'100%':>7s} "
        f"{fc_total:>8.2f}{fc_win} "
        f"{fl_total:>9.2f}{fl_win}"
    )


def print_cost_projection(fc_summaries: list[URLSummary], fl_summaries: list[URLSummary]) -> None:
    print_section("Cost Projection (paid tiers)")

    fc_successful = [s for s in fc_summaries if s.successes > 0]
    fl_successful = [s for s in fl_summaries if s.successes > 0]

    fc_cost = mean(s.cost_per_page for s in fc_successful) if fc_successful else 0
    fl_cost = mean(s.cost_per_page for s in fl_successful) if fl_successful else 0

    p(f"  {'Scale':<15s} {'Firecrawl':>12s} {'Flarecrawl':>12s} {'Savings':>12s}")
    p(f"  {'-' * 55}")
    for n, label in [
        (100, "100 pages"),
        (1_000, "1K pages"),
        (10_000, "10K pages"),
        (100_000, "100K pages"),
    ]:
        fc = fc_cost * n
        # Flarecrawl: browser rendering cost + $5/month Workers base
        fl = fl_cost * n + FLARECRAWL_WORKERS_BASE
        diff = abs(fc - fl)
        winner = "FC wins" if fl > fc else "FL wins" if fc > fl else "tie"
        p(f"  {label:<15s} ${fc:>11.2f} ${fl:>11.2f} ${diff:>9.2f} ({winner})")

    p()
    p(f"  Firecrawl: $99/mo Scale plan (100K credits)")
    p(f"  Flarecrawl: $5/mo Workers Paid + $0.09/hr browser rendering")
    p()
    fc_free = FIRECRAWL_FREE_CREDITS
    fl_avg_ms = mean(s.avg_browser_ms for s in fl_successful) if fl_successful else 3000
    fl_free = int(FLARECRAWL_FREE_MS / fl_avg_ms) if fl_avg_ms > 0 else 0
    p(f"  Free tier:  Firecrawl ~{fc_free} pages/month  |  Flarecrawl ~{fl_free} pages/day")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Benchmark Firecrawl vs Flarecrawl")
    parser.add_argument("--runs", type=int, default=3, help="Runs per URL per tool (default: 3)")
    parser.add_argument("--tool", choices=["firecrawl", "flarecrawl", "both"], default="both")
    parser.add_argument("--urls-only", action="store_true", help="List test URLs and exit")
    parser.add_argument("--output", type=str, help="Save raw results to JSON file")
    parser.add_argument("--timeout", type=int, default=60, help="Per-request timeout in seconds")
    args = parser.parse_args()

    if args.urls_only:
        for u in TEST_URLS:
            p(f"  [{u['category']:15s}] {u['url']}")
        return

    # Check tools are available
    tools = []
    if args.tool in ("firecrawl", "both"):
        if shutil.which("firecrawl"):
            if os.environ.get("FIRECRAWL_API_KEY"):
                tools.append("firecrawl")
            else:
                p("WARNING: FIRECRAWL_API_KEY not set, skipping firecrawl")
        else:
            p("WARNING: firecrawl CLI not found, skipping")

    if args.tool in ("flarecrawl", "both"):
        if shutil.which("flarecrawl"):
            tools.append("flarecrawl")
        else:
            p("WARNING: flarecrawl CLI not found, skipping")

    if not tools:
        p("ERROR: No tools available to benchmark")
        sys.exit(1)

    print_header("Firecrawl vs Flarecrawl Benchmark")
    p(f"  Tools:    {', '.join(tools)}")
    p(f"  URLs:     {len(TEST_URLS)}")
    p(f"  Runs:     {args.runs} per URL per tool")
    p(f"  Total:    {len(TEST_URLS) * args.runs * len(tools)} requests")

    # Collect results
    all_results: dict[str, list[ScrapeResult]] = {t: [] for t in tools}

    print_section("Running Benchmarks")

    for url_config in TEST_URLS:
        url = url_config["url"]
        for tool in tools:
            for run in range(1, args.runs + 1):
                result = run_single(tool, url_config, run)
                all_results[tool].append(result)
                print_progress(tool, url.replace("https://", "")[:55], run, args.runs, result)

                # Small delay to avoid rate limiting
                if run < args.runs or tool != tools[-1]:
                    time.sleep(1)

    # Aggregate per URL
    summaries: dict[str, list[URLSummary]] = {t: [] for t in tools}
    for tool in tools:
        for url_config in TEST_URLS:
            url = url_config["url"]
            url_results = [r for r in all_results[tool] if r.url == url]
            summary = aggregate(url_results, url_config, tool)
            summaries[tool].append(summary)

    # Content similarity
    similarities = []
    if "firecrawl" in tools and "flarecrawl" in tools:
        for url_config in TEST_URLS:
            url = url_config["url"]
            fc_results = [r for r in all_results["firecrawl"] if r.url == url and r.success]
            fl_results = [r for r in all_results["flarecrawl"] if r.url == url and r.success]
            if fc_results and fl_results:
                sim = content_similarity(fc_results[0].content, fl_results[0].content)
            else:
                sim = 0.0
            similarities.append({"url": url, "similarity": sim})

    # Display results
    print_header("Results")

    if "firecrawl" in tools and "flarecrawl" in tools:
        print_comparison_table(summaries["firecrawl"], summaries["flarecrawl"])
        print_similarity(similarities)

        fc_scores = score_tool(summaries["firecrawl"], summaries["flarecrawl"])
        fl_scores = score_tool(summaries["flarecrawl"], summaries["firecrawl"])
        print_scores(fc_scores, fl_scores)
        print_cost_projection(summaries["firecrawl"], summaries["flarecrawl"])
    else:
        tool = tools[0]
        print_section(f"{tool} Results")
        for s in summaries[tool]:
            short_url = s.url.replace("https://", "")[:50]
            p(
                f"  {short_url:<52s} {s.avg_elapsed_s:.1f}s  "
                f"{s.avg_content_length:>6,d} chars  "
                f"{s.success_rate:.0%} success"
            )

    # Save raw results
    if args.output:
        output_data = {
            "metadata": {
                "runs": args.runs,
                "tools": tools,
                "urls": [u["url"] for u in TEST_URLS],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            "results": {
                tool: [asdict(r) for r in all_results[tool]] for tool in tools
            },
            "summaries": {
                tool: [asdict(s) for s in summaries[tool]] for tool in tools
            },
        }
        if similarities:
            output_data["similarities"] = similarities
        if "firecrawl" in tools and "flarecrawl" in tools:
            output_data["scores"] = {
                "firecrawl": fc_scores,
                "flarecrawl": fl_scores,
            }

        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output_data, indent=2, default=str), encoding="utf-8")
        p(f"\n  Raw results saved to {args.output}")

    p()


if __name__ == "__main__":
    main()

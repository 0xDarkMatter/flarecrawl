---
name: autoresearch
description: "Autonomous improvement loop — iterate, test, benchmark, keep/revert, log. Triggers: autoresearch, continuous improvement, iterate and improve, benchmark loop, self-improving"
allowed-tools: "Read Write Edit Bash Glob Grep Agent TaskCreate TaskUpdate TaskList"
---

# Autoresearch: Autonomous Improvement Loop

Run a self-directed improvement loop on any measurable codebase metric. Inspired by [uditgoenka/autoresearch](https://github.com/uditgoenka/autoresearch).

## Core Protocol

```
For each iteration:
  1. REVIEW  — Read git log + results log (what worked/failed/untried)
  2. PICK    — Choose ONE focused change (highest expected impact)
  3. MAKE    — Implement the change (single file if possible)
  4. TEST    — Run test suite (must pass or revert)
  5. COMMIT  — Git commit with "experiment:" prefix BEFORE verification
  6. VERIFY  — Run mechanical benchmark/metric
  7. DECIDE  — If improved → keep. If worse → git revert
  8. LOG     — Append result to TSV log
  9. REPEAT  — Go to step 1
```

## Setup

### 1. Define Your Metric

The loop needs a **mechanical, numeric metric** — no subjective evaluation.

Examples:
- Benchmark score (weighted total)
- Test pass rate
- Response time (p50, p95)
- Bundle size
- Lines of code
- Error count

### 2. Create Results Log

Create a TSV file to track iterations:

```
iteration	commit	metric	delta	status	description
0	baseline	4.55	0	baseline	Initial measurement
```

### 3. Create Baseline

```bash
# Run your metric measurement
python tests/bench.py --runs 3 --output tests/bench-results-baseline.json

# Record baseline in TSV
```

## Loop Rules

### Rule 1: ONE Change Per Iteration
Each iteration makes exactly one focused change. This enables precise failure attribution.

### Rule 2: Tests Must Pass
Run the full test suite after every change. If tests fail, fix them before proceeding. Never skip tests.

### Rule 3: Commit Before Verification
Git commit with `experiment:` prefix BEFORE running the benchmark. This preserves the exact state for later analysis.

### Rule 4: Mechanical Metrics Only
Never evaluate subjectively. Use automated scoring, timing, or counting. If you can't measure it, don't optimize it.

### Rule 5: Keep or Revert
After benchmark:
- **Improved** → Keep the commit, log as `keep`
- **Unchanged** → Keep if the change has other benefits, log as `neutral`
- **Regressed** → `git revert HEAD`, log as `reverted`

### Rule 6: Git as Memory
Before each iteration, read:
- `git log --oneline -20` — recent changes
- Results TSV — what worked and what didn't
- Don't repeat failed approaches unless you have a genuinely new angle

### Rule 7: Think Harder When Stuck
If the last 3 iterations showed no improvement:
1. Re-read the metric formula — what exactly determines the score?
2. Re-read the raw data — where are the actual bottlenecks?
3. Try a radical approach (different algorithm, different architecture)
4. Consider if the metric ceiling has been reached

### Rule 8: Track Everything
The TSV log is your lab notebook. Include enough detail to understand each experiment months later.

## Task Management

Use Claude Code's TaskCreate/TaskUpdate for the work queue:

```
TaskCreate: "Improve speed via connection pooling"
  → status: in_progress when working
  → status: completed when done
  → description updated with results
```

Group related changes into rounds:
- Round 1: Low-hanging fruit (config changes, test fixes)
- Round 2: Architecture changes (pooling, caching)
- Round 3: Algorithm improvements (smart fallbacks)

## Example: Flarecrawl Benchmark Loop

### Metric
Weighted benchmark score across 5 dimensions (speed, content, cost, reliability, output quality).

### Iterations
```
Iter  Commit   Score  Delta  Status  Description
0     base     4.55   0      base    Initial scores
1     b47567c  -      -      keep    networkidle0 for JS rendering
2     30f48b4  -      -      keep    Enrich metadata (5 fields)
3     4376cf7  -      -      keep    File-based response cache (7x speedup)
4     c807746  4.15   -0.40  revert  networkidle2 caused timeouts
5     c6c4ca1  4.35   -0.20  keep    Reverted networkidle, added --wait-until flag
7     aa8becc  4.45   +0.10  keep    Smart JS fallback with retry
9     b8a15d4  -      -      keep    httpx connection pooling (HTTP/2)
10    08feecc  -      -      keep    Fix known_text test config
11    5eb173a  -      -      keep    Metadata enrichment to 11 fields
12    round2   4.80   +0.35  keep    Content accuracy + output quality → 5
```

### Key Lessons
- **networkidle0 as default was a bad idea** — some pages never reach idle (analytics). Smart fallback with timeout + retry was the solution.
- **Test config bugs matter** — blog.cloudflare known_text was wrong ("Browser Rendering" vs "browser-rendering"). Fixing the test gave a free +1 to content_accuracy.
- **Metadata is cheap** — parsing headings/links/wordcount from existing markdown costs zero API calls but pushed output_quality from 4 to 5.
- **Connection pooling** — replacing one-shot `httpx.get()` with persistent `httpx.Client()` reuses TCP+TLS across requests.

## Anti-Patterns

| Anti-Pattern | Why It Fails | Instead |
|-------------|-------------|---------|
| Multiple changes per iteration | Can't attribute improvement/regression | One change at a time |
| Subjective evaluation | "Looks better" isn't measurable | Mechanical metrics only |
| Deleting failed experiments | Lose information about what doesn't work | Git revert preserves history |
| Optimizing the wrong thing | Spending hours on a 10% weight dimension | Prioritize by weight × gap |
| Ignoring test failures | Regressions compound | Tests must pass every iteration |
| Not logging | Can't learn from past iterations | TSV log is mandatory |

## Invocation

```
/autoresearch

# Or with specific target:
"Run an autoresearch loop to improve [metric] by [approach]"
```

The skill works best when:
1. You have a clear, measurable metric
2. The codebase has a test suite
3. Changes can be made incrementally
4. Git is available for version control

# Flarecrawl docs

Index of the `docs/` tree. **Read the category before the file** — a design
spec or an archived plan is not current-state documentation.

> Canonical references live at the repo root, not here: **[README.md](../README.md)**
> (users) and **[AGENTS.md](../AGENTS.md)** (agents — also served by
> `flarecrawl guide`). This folder holds deeper reports, process docs, and
> frozen history.

## Current — accurate as of the latest release

| Doc | What it is |
|-----|------------|
| [HARD-TARGETS.md](HARD-TARGETS.md) | The hard-target escalation ladder (stealth → local Chromium → capture → p6). Live, all flags current. |
| [tech-detect-vs-w3techs.md](tech-detect-vs-w3techs.md) | `tech-detect` accuracy comparison vs W3Techs. Stand-alone report. |
| [custom-overlay-validation.md](custom-overlay-validation.md) | Validation of the custom Wappalyzer fingerprint overlay. |
| [RELEASING.md](RELEASING.md) | Release process + lessons-learned checklist. Evergreen. |
| [FEATURE-EVALUATION.md](FEATURE-EVALUATION.md) | Feature-evaluation matrix (built / deferred). Rolling planning record. |

## Reports — point-in-time site audits

| Doc | What it is |
|-----|------------|
| [reports/portrait-gov-au.md](reports/portrait-gov-au.md) | Tech-detection audit of a specific target. Snapshot, not maintained. |

## Design & research — frozen specs (read as history, not current state)

| Doc | What it is |
|-----|------------|
| [research/FRONTIER-COMPARISON.md](research/FRONTIER-COMPARISON.md) | Crawler-frontier survey that informed the frontier v2 design. Spec, not current-state doc. |

## Archive — superseded plans and shipped design records

Frozen by design. They document how things were built; they are **not** updated
to match current code.

| Doc | What it is |
|-----|------------|
| [BENCHMARK-REPORT.md](BENCHMARK-REPORT.md) | Firecrawl-vs-Flarecrawl bench. Historical baseline (~v0.3.0-era, 2026-03-19) — banner at top. |
| [archive/PERF-PLAN-PROGRESS.md](archive/PERF-PLAN-PROGRESS.md) | v0.16–v0.17 performance campaign delivery log. |
| [archive/UPGRADE-PLAN-v0.23.0.md](archive/UPGRADE-PLAN-v0.23.0.md) | Hard-target stack upgrade spec (v0.23–v0.25). |
| [archive/UPGRADE-PLAN-v0.26.0.md](archive/UPGRADE-PLAN-v0.26.0.md) | Headless-evasion (humanize) upgrade spec. |
| [archive/architect-v0.14.0-prompt.md](archive/architect-v0.14.0-prompt.md) | One-time Forma Architect scaffolding prompt (v0.13→v0.14). References the pre-split `cli.py`. |
| [archive/mcp-design/](archive/mcp-design/) | MCP surface design manifest + wiring/docs snippets. Shipped in v0.31.0; kept as design record. |

> `BENCHMARK-REPORT.md` sits at the `docs/` root for historical reasons but is
> archive-grade — treat its numbers as a baseline, not current behaviour.

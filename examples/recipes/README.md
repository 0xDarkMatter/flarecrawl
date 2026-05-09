# Recipe examples

Each `.yml` file is a standalone, runnable recipe.

| Recipe | Demonstrates |
|--------|-------------|
| [war-gov-uap.yml](war-gov-uap.yml) | Local headed Chromium + stealth init + response body capture (P2.1/P2.2/P3.3) — the v0.23.0–v0.25.0 dogfood |

Run any recipe with:

```bash
flarecrawl recipe examples/recipes/war-gov-uap.yml
flarecrawl recipe examples/recipes/war-gov-uap.yml --dry-run
flarecrawl recipe examples/recipes/war-gov-uap.yml --resume
```

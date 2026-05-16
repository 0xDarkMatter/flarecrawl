# Flarecrawl Routing Fixture

This file is served as `text/markdown` and must be returned **verbatim**
by the fetch command without any HTML conversion or CF Browser Rendering.

## Purpose

Tests that the `flarecrawl fetch` command correctly routes non-HTML content
types to the raw-text branch, bypassing Cloudflare Browser Rendering entirely.

## Routing matrix

| Content-Type | Branch |
|---|---|
| `text/html`, `application/xhtml+xml` | CF Browser Rendering → markdown |
| `application/json`, `application/*+json` | JSON parse → return as-is |
| `image/*`, `audio/*`, `video/*`, `application/pdf` | Binary download → file |
| Everything else | **Raw text → return verbatim** ← this file |

## Usage

```bash
flarecrawl fetch http://localhost:8787/README.md --json
```

The `data` field in the JSON response will contain this markdown exactly as
stored on disk, with no transformation applied.

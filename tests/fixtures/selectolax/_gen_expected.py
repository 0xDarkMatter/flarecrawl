"""One-shot helper: captures pre-migration BS4 extract.py outputs to JSON.

Run once against BS4 implementation, then diff against post-migration selectolax
implementation. After both agree, this script can stay for regeneration.

Usage: PYTHONPATH=src python tests/fixtures/selectolax/_gen_expected.py
"""

from __future__ import annotations

import json
from pathlib import Path

from flarecrawl.extract import (
    clean_html,
    extract_accessibility_tree,
    extract_images,
    extract_main_content,
    extract_main_content_precision,
    extract_main_content_recall,
    extract_structured_data,
    filter_tags,
    html_to_markdown,
)

FIXTURES = ["blog", "news", "spa"]
BASE = Path(__file__).parent
BASE_URL = "https://example.com/page"


def snapshot(html: str) -> dict:
    """Collect one snapshot across all extract.py entry points."""
    return {
        "extract_main_content": extract_main_content(html),
        "extract_main_content_precision": extract_main_content_precision(html),
        "extract_main_content_recall": extract_main_content_recall(html),
        "filter_tags_include_h2": filter_tags(html, include=["h2"]),
        "filter_tags_exclude_nav": filter_tags(html, exclude=["nav", "footer"]),
        "extract_images": extract_images(html, BASE_URL),
        "extract_structured_data": extract_structured_data(html),
        "html_to_markdown": html_to_markdown(html),
        "extract_accessibility_tree": extract_accessibility_tree(html),
        "clean_html": clean_html(html),
    }


def main() -> None:
    for name in FIXTURES:
        html = (BASE / f"{name}.html").read_text(encoding="utf-8")
        data = snapshot(html)
        out = BASE / f"{name}.expected.json"
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        print(f"Wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

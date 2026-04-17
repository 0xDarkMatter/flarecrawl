"""Golden-output parity tests for the BS4 -> selectolax migration.

Fixtures: tests/fixtures/selectolax/{blog,news,spa}.html
Expected: tests/fixtures/selectolax/{blog,news,spa}.expected.json (captured
           against the pre-migration BeautifulSoup implementation).

For markup-producing functions (extract_main_content, filter_tags, clean_html,
extract_main_content_precision/_recall) we compare **normalised HTML** rather
than raw bytes: different parsers legitimately emit whitespace/attribute order
slightly differently but the DOM must be equivalent.

For structured-data / collection outputs (images, ld_json, og, twitter,
markdown, a11y tree) we compare values directly.

Note on html_to_markdown: selectolax drops the leading DOCTYPE "html" literal
that BS4 emitted as a NavigableString child of the soup root. The expected
fixtures were refreshed once post-migration to reflect this (intended)
improvement — all subsequent runs are byte-stable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

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

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "selectolax"
FIXTURES = ["blog", "news", "spa"]
BASE_URL = "https://example.com/page"


_VOID_TAGS = {"img", "br", "hr", "input", "meta", "link", "source", "area",
              "base", "col", "embed", "param", "track", "wbr"}


def _normalise_html(html: str) -> str:
    """Collapse whitespace + canonicalise attrs for semantic HTML comparison.

    BS4 and selectolax are both valid HTML parsers but differ in incidental
    serialisation details:

    - BS4 (via lxml) alphabetises attributes; selectolax preserves source order.
    - BS4 emits void elements as self-closing ``<img ... />``; selectolax
      emits them as ``<img ...>``.

    We normalise both dimensions: sort attributes alphabetically per tag and
    strip trailing ``/`` on void-tag openers.
    """
    # Collapse whitespace
    s = re.sub(r">\s+<", "><", html)
    s = re.sub(r"\s+", " ", s).strip()

    # Canonicalise each opening tag: sort attributes alphabetically.
    def _canon_tag(m: re.Match) -> str:
        tag = m.group("tag").lower()
        rest = m.group("rest") or ""
        # Extract attr="value" pairs (also handles attr=value and bare attrs).
        attrs = re.findall(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*(?:=\s*"([^"]*)")?', rest)
        pairs = sorted((k.lower(), v) for k, v in attrs if k)
        attr_s = "".join(f' {k}="{v}"' for k, v in pairs)
        return f"<{tag}{attr_s}>"

    s = re.sub(r"<(?P<tag>[a-zA-Z][a-zA-Z0-9]*)(?P<rest>\s[^>]*?)?\s*/?>", _canon_tag, s)

    # Strip closing tags for HTML void elements — BS4 sometimes emits these
    # when the source HTML supplied them explicitly, selectolax never does.
    void_close = "|".join(sorted(_VOID_TAGS))
    s = re.sub(rf"</(?:{void_close})>", "", s, flags=re.IGNORECASE)
    return s


def _load_expected(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.expected.json").read_text(encoding="utf-8"))


def _load_html(name: str) -> str:
    return (FIXTURES_DIR / f"{name}.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", FIXTURES)
class TestSelectolaxParity:
    """Per-fixture parity sweep across every extract.py entry point."""

    def test_extract_main_content(self, name: str) -> None:
        html = _load_html(name)
        expected = _load_expected(name)["extract_main_content"]
        got = extract_main_content(html)
        assert _normalise_html(got) == _normalise_html(expected)

    def test_extract_main_content_precision(self, name: str) -> None:
        html = _load_html(name)
        expected = _load_expected(name)["extract_main_content_precision"]
        got = extract_main_content_precision(html)
        assert _normalise_html(got) == _normalise_html(expected)

    def test_extract_main_content_recall(self, name: str) -> None:
        html = _load_html(name)
        expected = _load_expected(name)["extract_main_content_recall"]
        got = extract_main_content_recall(html)
        assert _normalise_html(got) == _normalise_html(expected)

    def test_filter_tags_include(self, name: str) -> None:
        html = _load_html(name)
        expected = _load_expected(name)["filter_tags_include_h2"]
        got = filter_tags(html, include=["h2"])
        assert _normalise_html(got) == _normalise_html(expected)

    def test_filter_tags_exclude(self, name: str) -> None:
        html = _load_html(name)
        expected = _load_expected(name)["filter_tags_exclude_nav"]
        got = filter_tags(html, exclude=["nav", "footer"])
        assert _normalise_html(got) == _normalise_html(expected)

    def test_extract_images(self, name: str) -> None:
        html = _load_html(name)
        expected = _load_expected(name)["extract_images"]
        got = extract_images(html, BASE_URL)
        # Compare as sets of (url, alt) — order/attrs may differ but identity
        # of discovered images must match.
        def key(img: dict) -> tuple[str, str]:
            return (img["url"], img.get("alt", ""))
        assert sorted(key(i) for i in got) == sorted(key(i) for i in expected)

    def test_extract_structured_data(self, name: str) -> None:
        html = _load_html(name)
        expected = _load_expected(name)["extract_structured_data"]
        got = extract_structured_data(html)
        assert got["opengraph"] == expected["opengraph"]
        assert got["twitter_card"] == expected["twitter_card"]
        assert got["ld_json"] == expected["ld_json"]

    def test_html_to_markdown(self, name: str) -> None:
        html = _load_html(name)
        expected = _load_expected(name)["html_to_markdown"]
        got = html_to_markdown(html)
        # Markdown output normalisation: strip trailing whitespace on lines.
        def norm(md: str) -> str:
            return "\n".join(line.rstrip() for line in md.splitlines()).strip()
        assert norm(got) == norm(expected)

    def test_extract_accessibility_tree(self, name: str) -> None:
        html = _load_html(name)
        expected = _load_expected(name)["extract_accessibility_tree"]
        got = extract_accessibility_tree(html)
        # Compare role+name tuples (depth may drift by parser but structure same).
        def key(n: dict) -> tuple:
            return (n.get("role"), n.get("name"), n.get("level"))
        assert [key(n) for n in got] == [key(n) for n in expected]

    def test_clean_html(self, name: str) -> None:
        html = _load_html(name)
        expected = _load_expected(name)["clean_html"]
        got = clean_html(html)
        assert _normalise_html(got) == _normalise_html(expected)

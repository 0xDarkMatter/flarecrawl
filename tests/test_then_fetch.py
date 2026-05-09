"""Tests for v0.24.0 P2.3: --then-fetch URL list parsing.

The download path itself is exercised end-to-end in tests/live/. These
tests cover only the pure-Python URL-list parsing logic, which is the
bug-prone part.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _call_with_mocked_cdp(tmp_path, **kwargs):
    """Invoke _run_then_fetch with a mocked CDP client + downloads.

    Returns the summary dict.
    """
    from flarecrawl.cli import _run_then_fetch

    fake_page = MagicMock()
    fake_page.get_cookies.return_value = []
    fake_cdp = MagicMock()
    fake_cdp.new_page.return_value = fake_page

    # Patch download_binary_stealth where _run_then_fetch imports it
    fake_result = MagicMock()
    fake_result.size = 100
    fake_result.path = tmp_path / "fake"

    with patch("flarecrawl.fetch.download_binary_stealth", return_value=fake_result):
        return _run_then_fetch(
            cdp_client=fake_cdp,
            then_fetch_output=tmp_path,
            then_fetch_workers=2,
            json_output=True,  # silence console
            then_fetch_column=kwargs.get("column"),
            then_fetch=kwargs.get("inline"),
            then_fetch_from=kwargs.get("from_file"),
        )


def test_inline_csv_string(tmp_path):
    out = _call_with_mocked_cdp(
        tmp_path,
        inline="https://x.com/a.pdf,https://x.com/b.pdf",
    )
    assert out["total"] == 2
    assert out["output_dir"] == str(tmp_path)


def test_text_file_one_per_line(tmp_path):
    src = tmp_path / "urls.txt"
    src.write_text(
        "https://x.com/a.pdf\n"
        "# this is a comment, ignore\n"
        "\n"
        "https://x.com/b.pdf\n"
        "https://x.com/c.pdf\n",
        encoding="utf-8",
    )
    out = _call_with_mocked_cdp(tmp_path, from_file=src)
    assert out["total"] == 3


def test_csv_column_extraction(tmp_path):
    src = tmp_path / "manifest.csv"
    src.write_text(
        "Title,PDF Link,Notes\n"
        "Foo,https://x.com/foo.pdf,important\n"
        "Bar,https://x.com/bar.pdf,less\n"
        "Baz,not-a-url,skip me\n"
        "Qux,https://x.com/qux.pdf,\n",
        encoding="utf-8",
    )
    out = _call_with_mocked_cdp(tmp_path, from_file=src, column="PDF Link")
    # Three valid http URLs (qux included), the "not-a-url" row is skipped
    assert out["total"] == 3


def test_csv_column_with_special_characters_in_name(tmp_path):
    """Column names like 'PDF | Image Link' (war.gov real-world)."""
    src = tmp_path / "manifest.csv"
    src.write_text(
        '"Title","PDF | Image Link"\n'
        '"A","https://x.com/a.pdf"\n'
        '"B","https://x.com/b.pdf"\n',
        encoding="utf-8",
    )
    out = _call_with_mocked_cdp(tmp_path, from_file=src, column="PDF | Image Link")
    assert out["total"] == 2


def test_dedupe_preserves_order(tmp_path):
    out = _call_with_mocked_cdp(
        tmp_path,
        inline="https://x.com/a,https://x.com/b,https://x.com/a,https://x.com/c",
    )
    assert out["total"] == 3


def test_inline_plus_from_file_concatenated(tmp_path):
    src = tmp_path / "extra.txt"
    src.write_text("https://x.com/c\nhttps://x.com/d\n")
    out = _call_with_mocked_cdp(
        tmp_path,
        inline="https://x.com/a,https://x.com/b",
        from_file=src,
    )
    assert out["total"] == 4


class TestOrganizeBy:
    """v0.25.1: classify URLs into subdirs by extension / content-type / thumbnail."""

    def test_flat_default(self):
        from flarecrawl.cli import _classify_url_for_organize
        assert _classify_url_for_organize("https://x.com/foo.pdf", "flat") == ""
        assert _classify_url_for_organize("https://x.com/foo.pdf", None) == ""

    def test_extension_pdfs(self):
        from flarecrawl.cli import _classify_url_for_organize
        assert _classify_url_for_organize("https://x.com/foo.pdf", "extension") == "pdfs"
        assert _classify_url_for_organize("https://x.com/foo.PDF?q=1", "extension") == "pdfs"

    def test_extension_images(self):
        from flarecrawl.cli import _classify_url_for_organize
        for ext in ("jpg", "jpeg", "png", "gif", "webp", "svg"):
            assert _classify_url_for_organize(f"https://x.com/foo.{ext}", "extension") == "images"

    def test_extension_videos(self):
        from flarecrawl.cli import _classify_url_for_organize
        for ext in ("mp4", "webm", "mov", "avi", "mkv"):
            assert _classify_url_for_organize(f"https://x.com/clip.{ext}", "extension") == "videos"

    def test_extension_other(self):
        from flarecrawl.cli import _classify_url_for_organize
        assert _classify_url_for_organize("https://x.com/strange.xyz", "extension") == "other"
        assert _classify_url_for_organize("https://x.com/no-extension", "extension") == "other"

    def test_thumbnail_mode_special_cases_thumbnails(self):
        from flarecrawl.cli import _classify_url_for_organize
        # war.gov pattern: /thumbnail/ in path
        assert _classify_url_for_organize(
            "https://www.war.gov/medialink/ufo/release_1/thumbnail/x.jpg",
            "thumbnail",
        ) == "thumbnails"
        # Filename containing 'thumbnail'
        assert _classify_url_for_organize(
            "https://x.com/files/foo-thumbnail.jpg",
            "thumbnail",
        ) == "thumbnails"

    def test_thumbnail_mode_falls_through_to_extension(self):
        from flarecrawl.cli import _classify_url_for_organize
        # Non-thumbnail PDF still gets classified
        assert _classify_url_for_organize(
            "https://www.war.gov/medialink/ufo/release_1/foo.pdf",
            "thumbnail",
        ) == "pdfs"

    def test_content_type_mode(self):
        from flarecrawl.cli import _classify_url_for_organize
        assert _classify_url_for_organize("https://x.com/a.jpg", "content-type") == "image"
        assert _classify_url_for_organize("https://x.com/a.mp4", "content-type") == "video"
        assert _classify_url_for_organize("https://x.com/a.mp3", "content-type") == "audio"
        assert _classify_url_for_organize("https://x.com/a.pdf", "content-type") == "application"


def test_skip_existing_files(tmp_path):
    """Resume-safety: existing non-empty files skip the download."""
    from flarecrawl.cli import _run_then_fetch

    # Pre-create one of the destinations
    (tmp_path / "a.pdf").write_bytes(b"already downloaded")

    fake_page = MagicMock()
    fake_page.get_cookies.return_value = []
    fake_cdp = MagicMock()
    fake_cdp.new_page.return_value = fake_page

    with patch("flarecrawl.fetch.download_binary_stealth") as mock_dl:
        mock_dl.side_effect = lambda url, dest, **kw: MagicMock(size=200, path=dest)
        out = _run_then_fetch(
            cdp_client=fake_cdp,
            then_fetch="https://x.com/a.pdf,https://x.com/b.pdf",
            then_fetch_from=None,
            then_fetch_column=None,
            then_fetch_output=tmp_path,
            then_fetch_workers=1,
            json_output=True,
        )
    # download_binary_stealth should have been called only for b.pdf
    called_urls = [c.args[0] for c in mock_dl.call_args_list]
    assert called_urls == ["https://x.com/b.pdf"]
    assert out["total"] == 2

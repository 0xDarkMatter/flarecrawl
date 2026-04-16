"""Tests for content-type detection and filename derivation."""

import pytest

from flarecrawl.fetch import (
    ContentInfo,
    DownloadResult,
    _filename_from_url,
    _is_binary_content_type,
    _parse_content_disposition,
    build_session,
)


class TestIsBinaryContentType:
    """Test _is_binary_content_type()."""

    def test_html_is_not_binary(self):
        assert not _is_binary_content_type("text/html")

    def test_plain_text_is_not_binary(self):
        assert not _is_binary_content_type("text/plain")

    def test_json_is_not_binary(self):
        assert not _is_binary_content_type("application/json")

    def test_xml_is_not_binary(self):
        assert not _is_binary_content_type("application/xml")

    def test_pdf_is_binary(self):
        assert _is_binary_content_type("application/pdf")

    def test_zip_is_binary(self):
        assert _is_binary_content_type("application/zip")

    def test_image_is_binary(self):
        assert _is_binary_content_type("image/png")
        assert _is_binary_content_type("image/jpeg")

    def test_audio_is_binary(self):
        assert _is_binary_content_type("audio/mpeg")

    def test_video_is_binary(self):
        assert _is_binary_content_type("video/mp4")

    def test_font_is_binary(self):
        assert _is_binary_content_type("font/woff2")

    def test_content_type_with_charset_is_not_binary(self):
        assert not _is_binary_content_type("text/html; charset=utf-8")

    def test_octet_stream_is_binary(self):
        assert _is_binary_content_type("application/octet-stream")


class TestParseContentDisposition:
    """Test Content-Disposition filename parsing."""

    def test_simple_filename(self):
        result = _parse_content_disposition('attachment; filename="report.pdf"')
        assert result == "report.pdf"

    def test_filename_without_quotes(self):
        result = _parse_content_disposition("attachment; filename=report.pdf")
        assert result == "report.pdf"

    def test_rfc5987_filename(self):
        result = _parse_content_disposition("attachment; filename*=UTF-8''report%202024.pdf")
        assert result == "report 2024.pdf"

    def test_none_returns_none(self):
        assert _parse_content_disposition(None) is None

    def test_empty_returns_none(self):
        assert _parse_content_disposition("") is None

    def test_no_filename_returns_none(self):
        assert _parse_content_disposition("inline") is None


class TestFilenameFromUrl:
    """Test URL-to-filename derivation."""

    def test_simple_path(self):
        assert _filename_from_url("https://example.com/report.pdf") == "report.pdf"

    def test_path_without_extension(self):
        result = _filename_from_url("https://example.com/downloads/file")
        assert result == "download"

    def test_root_path_returns_download(self):
        assert _filename_from_url("https://example.com/") == "download"

    def test_url_encoded_filename(self):
        result = _filename_from_url("https://example.com/my%20report.pdf")
        assert result == "my report.pdf"


class TestBuildSession:
    """Test build_session() configuration."""

    def test_returns_httpx_client(self):
        import httpx
        session = build_session()
        assert isinstance(session, httpx.Client)
        session.close()

    def test_with_auth(self):
        import httpx
        session = build_session(auth=("user", "pass"))
        assert session.auth is not None
        session.close()

    def test_with_headers(self):
        import httpx
        session = build_session(headers={"X-Custom": "value"})
        assert "X-Custom" in session.headers
        session.close()

    def test_with_cookies(self, tmp_path):
        import httpx
        cookies = [{"name": "tok", "value": "abc", "domain": ".example.com", "path": "/"}]
        session = build_session(cookies=cookies)
        assert isinstance(session, httpx.Client)
        session.close()


class TestDataclasses:
    """Test ContentInfo and DownloadResult dataclasses."""

    def test_content_info_fields(self):
        info = ContentInfo(
            content_type="application/pdf",
            size=1024,
            filename="doc.pdf",
            is_binary=True,
            is_json=False,
        )
        assert info.content_type == "application/pdf"
        assert info.is_binary is True
        assert info.is_json is False

    def test_download_result_fields(self):
        from pathlib import Path
        result = DownloadResult(
            path=Path("/tmp/doc.pdf"),
            content_type="application/pdf",
            size=2048,
            elapsed=1.5,
            filename="doc.pdf",
        )
        assert result.size == 2048
        assert result.elapsed == 1.5

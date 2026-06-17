"""Tests for v0.23.0 P1.4: stealth (curl_cffi) binary download path."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


def test_missing_curl_cffi_raises_helpful_error(tmp_path, monkeypatch):
    """When curl_cffi is not installed, raise an actionable ImportError."""
    from flarecrawl.fetch import download_binary_stealth

    sys.modules.pop("curl_cffi", None)
    sys.modules.pop("curl_cffi.requests", None)

    import builtins
    real_import = builtins.__import__

    def faux_import(name, *a, **kw):
        if name.startswith("curl_cffi"):
            raise ImportError("simulated")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", faux_import)

    with pytest.raises(ImportError) as exc_info:
        download_binary_stealth("https://example.com/x", tmp_path / "out.pdf")
    msg = str(exc_info.value)
    assert "curl_cffi" in msg
    assert "uv tool install" in msg


def test_writes_file_with_mock_session(tmp_path, monkeypatch):
    """Mock curl_cffi.Session — verify chunked write + DownloadResult shape."""
    from flarecrawl.fetch import download_binary_stealth

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {
        "content-type": "application/pdf",
        "content-disposition": 'attachment; filename="hello.pdf"',
    }
    fake_resp.iter_content = lambda chunk_size: iter([b"hello", b"world"])

    fake_session = MagicMock()
    fake_session.get.return_value = fake_resp
    fake_session.headers = {}

    fake_module = MagicMock()
    fake_module.requests.Session.return_value = fake_session

    monkeypatch.setitem(sys.modules, "curl_cffi", fake_module)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_module.requests)

    out = tmp_path / "out.pdf"
    result = download_binary_stealth("https://example.com/x.pdf", out)

    assert out.read_bytes() == b"helloworld"
    assert result.size == 10
    assert result.content_type == "application/pdf"
    assert result.filename == "hello.pdf"
    kwargs = fake_module.requests.Session.call_args.kwargs
    assert kwargs.get("impersonate") == "chrome131"


def test_4xx_raises_runtime_error(tmp_path, monkeypatch):
    from flarecrawl.fetch import download_binary_stealth

    fake_resp = MagicMock()
    fake_resp.status_code = 403
    fake_resp.headers = {}
    fake_resp.iter_content = lambda chunk_size: iter([])

    fake_session = MagicMock()
    fake_session.get.return_value = fake_resp
    fake_session.headers = {}

    fake_module = MagicMock()
    fake_module.requests.Session.return_value = fake_session

    monkeypatch.setitem(sys.modules, "curl_cffi", fake_module)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_module.requests)

    with pytest.raises(RuntimeError) as exc_info:
        download_binary_stealth("https://x.com/a.pdf", tmp_path / "a.pdf")
    assert "403" in str(exc_info.value)


def test_empty_body_raises_runtime_error(tmp_path, monkeypatch):
    """v0.26.0: empty 200 responses are treated as failures (no zero-byte file)."""
    from flarecrawl.fetch import download_binary_stealth

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"content-type": "application/pdf"}
    fake_resp.iter_content = lambda chunk_size: iter([])  # zero chunks

    fake_session = MagicMock()
    fake_session.get.return_value = fake_resp
    fake_session.headers = {}

    fake_module = MagicMock()
    fake_module.requests.Session.return_value = fake_session

    monkeypatch.setitem(sys.modules, "curl_cffi", fake_module)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_module.requests)

    out = tmp_path / "empty.pdf"
    with pytest.raises(RuntimeError, match="empty body"):
        download_binary_stealth("https://x.com/empty.pdf", out)
    assert not out.exists()  # zero-byte file should be cleaned up


def test_proxy_passed_to_session(tmp_path, monkeypatch):
    from flarecrawl.fetch import download_binary_stealth

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"content-type": "application/octet-stream"}
    fake_resp.iter_content = lambda chunk_size: iter([b"x"])

    fake_session = MagicMock()
    fake_session.get.return_value = fake_resp
    fake_session.headers = {}

    fake_module = MagicMock()
    fake_module.requests.Session.return_value = fake_session

    monkeypatch.setitem(sys.modules, "curl_cffi", fake_module)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_module.requests)

    download_binary_stealth(
        "https://x.com/file",
        tmp_path / "out.bin",
        proxy="http://proxy.example:8080",
    )
    kwargs = fake_module.requests.Session.call_args.kwargs
    assert kwargs.get("proxies") == {
        "http": "http://proxy.example:8080",
        "https": "http://proxy.example:8080",
    }


def test_custom_impersonate_profile(tmp_path, monkeypatch):
    from flarecrawl.fetch import download_binary_stealth

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"content-type": "application/octet-stream"}
    fake_resp.iter_content = lambda chunk_size: iter([b"x"])

    fake_session = MagicMock()
    fake_session.get.return_value = fake_resp
    fake_session.headers = {}

    fake_module = MagicMock()
    fake_module.requests.Session.return_value = fake_session

    monkeypatch.setitem(sys.modules, "curl_cffi", fake_module)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_module.requests)

    download_binary_stealth(
        "https://x.com/file",
        tmp_path / "out.bin",
        impersonate="safari17",
    )
    kwargs = fake_module.requests.Session.call_args.kwargs
    assert kwargs.get("impersonate") == "safari17"

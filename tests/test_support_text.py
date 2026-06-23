"""Tests for the operator's custom support text loader.

read_custom_support_text() is a pure function over a file + settings, so it
is exercised in-process here rather than through the live server (the test
stack runs the server in a separate process where monkeypatching settings
wouldn't reach it). The server-facing check is just that the config endpoint
carries the key.
"""

import httpx
import pytest

from sheaf import config
from sheaf.config import read_custom_support_text, settings


@pytest.fixture
def _reset_support_cache():
    # The loader memoises on the file's (mtime, size); reset around each test
    # so a previous test's path can't leak in.
    config._support_text_cache = None
    yield
    config._support_text_cache = None


def test_unset_returns_none(monkeypatch, _reset_support_cache):
    monkeypatch.setattr(settings, "custom_support_text_file", "")
    assert read_custom_support_text() is None


def test_missing_file_returns_none(tmp_path, monkeypatch, _reset_support_cache):
    monkeypatch.setattr(
        settings, "custom_support_text_file", str(tmp_path / "absent.md")
    )
    assert read_custom_support_text() is None


def test_strips_html_keeps_markdown(tmp_path, monkeypatch, _reset_support_cache):
    f = tmp_path / "support.md"
    f.write_text(
        "# Need help?\n\n"
        "Email <b>us</b> and read the [docs](https://example.com).\n"
        "<script>alert('xss')</script>\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "custom_support_text_file", str(f))

    text = read_custom_support_text()
    assert text is not None
    # Markdown survives untouched.
    assert "# Need help?" in text
    assert "[docs](https://example.com)" in text
    # Every HTML tag (and script body) is gone, stripped at load time rather
    # than left for a client to sanitise.
    for needle in ("<b>", "</b>", "<script>", "alert"):
        assert needle not in text


def test_caps_length(tmp_path, monkeypatch, _reset_support_cache):
    f = tmp_path / "big.md"
    f.write_text("A" * 50_000, encoding="utf-8")
    monkeypatch.setattr(settings, "custom_support_text_file", str(f))

    text = read_custom_support_text()
    assert text is not None
    assert len(text) <= config._MAX_SUPPORT_TEXT_CHARS


def test_picks_up_edits_without_restart(tmp_path, monkeypatch, _reset_support_cache):
    f = tmp_path / "support.md"
    f.write_text("First version", encoding="utf-8")
    monkeypatch.setattr(settings, "custom_support_text_file", str(f))
    assert read_custom_support_text() == "First version"

    # Different length changes the (mtime, size) signature, so the cached
    # value is invalidated and the new content is read.
    f.write_text("Second, longer version", encoding="utf-8")
    assert read_custom_support_text() == "Second, longer version"


def test_config_endpoint_exposes_key(client: httpx.Client):
    resp = client.get("/v1/auth/config")
    assert resp.status_code == 200
    # Always present; null unless the operator configured a file (the test
    # stack does not, so we only assert the contract, not a value).
    assert "support_custom_text" in resp.json()

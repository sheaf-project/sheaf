"""Tests for URL signing and resolution in sheaf/files.py."""

from urllib.parse import parse_qs, urlparse

import pytest

from sheaf.config import settings
from sheaf.files import (
    normalize_avatar_url,
    resolve_avatar_url,
    sign_cdn_url,
    sign_file_url,
    verify_file_token,
)


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch):
    """Each test starts with known settings and restores on exit."""
    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    monkeypatch.setattr(settings, "s3_public_url", "")
    monkeypatch.setattr(settings, "image_serving", "signed")
    monkeypatch.setattr(settings, "file_signing_key", "")
    monkeypatch.setattr(settings, "file_url_expiry_seconds", 3600)


def test_sign_cdn_url_shape(monkeypatch):
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    url = sign_cdn_url("avatars/user/abc.png")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "images.example.com"
    assert parsed.path == "/avatars/user/abc.png"
    q = parse_qs(parsed.query)
    assert len(q["token"][0]) == 64  # sha256 hex
    assert int(q["expires"][0]) > 0


def test_sign_cdn_url_strips_trailing_slash(monkeypatch):
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com/")
    url = sign_cdn_url("bios/user/x.png")
    assert url.startswith("https://images.example.com/bios/user/x.png?")


def test_sign_cdn_url_verifiable_with_verify_file_token(monkeypatch):
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    key = "avatars/user/abc.png"
    url = sign_cdn_url(key)
    q = parse_qs(urlparse(url).query)
    assert verify_file_token(key, q["token"][0], q["expires"][0])


def test_file_signing_key_overrides_jwt_derivation(monkeypatch):
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    key = "avatars/user/abc.png"

    # Token from jwt-derived key
    url_a = sign_cdn_url(key)
    token_a = parse_qs(urlparse(url_a).query)["token"][0]

    # Same key, same window, but with file_signing_key set → different HMAC
    monkeypatch.setattr(settings, "file_signing_key", "deadbeef" * 8)
    url_b = sign_cdn_url(key)
    token_b = parse_qs(urlparse(url_b).query)["token"][0]

    assert token_a != token_b
    # And a token signed with the override verifies under the override
    q_b = parse_qs(urlparse(url_b).query)
    assert verify_file_token(key, q_b["token"][0], q_b["expires"][0])


def test_resolve_avatar_url_s3_cdn_signed_routes_through_cdn(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    monkeypatch.setattr(settings, "image_serving", "signed")

    resolved = resolve_avatar_url("avatars/user/abc.png")
    assert resolved.startswith("https://images.example.com/avatars/user/abc.png?token=")
    assert "expires=" in resolved


def test_resolve_avatar_url_s3_cdn_unsigned_is_bare(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    monkeypatch.setattr(settings, "image_serving", "unsigned")

    assert (
        resolve_avatar_url("avatars/user/abc.png")
        == "https://images.example.com/avatars/user/abc.png"
    )


def test_resolve_avatar_url_filesystem_signed_uses_app_serve(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    monkeypatch.setattr(settings, "image_serving", "signed")

    resolved = resolve_avatar_url("avatars/user/abc.png")
    assert resolved.startswith("/v1/files/avatars/user/abc.png?token=")
    # And matches what sign_file_url would produce
    assert resolved == sign_file_url("avatars/user/abc.png")


def test_resolve_avatar_url_s3_without_public_url_falls_back_to_app_serve(monkeypatch):
    """S3 backend but no CDN configured → serve via the app, same as filesystem."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "")
    monkeypatch.setattr(settings, "image_serving", "signed")

    resolved = resolve_avatar_url("avatars/user/abc.png")
    assert resolved.startswith("/v1/files/avatars/user/abc.png?token=")


def test_resolve_avatar_url_external_url_passthrough():
    assert resolve_avatar_url("https://gravatar.com/x.png") == "https://gravatar.com/x.png"


def test_resolve_avatar_url_none():
    assert resolve_avatar_url(None) is None


def test_resolve_avatar_url_legacy_full_cdn_url_gets_signed(monkeypatch):
    """DB row written before CDN-aware code has the full CDN URL, no token.
    It should still be recognised and signed on read."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    monkeypatch.setattr(settings, "image_serving", "signed")

    stored = "https://images.example.com/avatars/user/abc.png"
    resolved = resolve_avatar_url(stored)
    assert resolved.startswith("https://images.example.com/avatars/user/abc.png?token=")
    assert "expires=" in resolved


def test_resolve_avatar_url_stored_signed_cdn_url_resigns(monkeypatch):
    """DB row containing a stale signed CDN URL should drop the old token
    and get a fresh one, not be returned verbatim."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    monkeypatch.setattr(settings, "image_serving", "signed")

    stored = "https://images.example.com/avatars/user/abc.png?token=deadbeef&expires=1"
    resolved = resolve_avatar_url(stored)
    assert "token=deadbeef" not in resolved
    assert "expires=1&" not in resolved and not resolved.endswith("expires=1")
    # And is in fact a valid signed URL
    q = parse_qs(urlparse(resolved).query)
    assert verify_file_token("avatars/user/abc.png", q["token"][0], q["expires"][0])


def test_normalize_avatar_url_strips_cdn_url_to_key(monkeypatch):
    """Writing a full CDN URL back to the DB should persist the bare key."""
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    stored = "https://images.example.com/avatars/user/abc.png?token=x&expires=1"
    assert normalize_avatar_url(stored) == "avatars/user/abc.png"


def test_normalize_avatar_url_external_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    assert (
        normalize_avatar_url("https://gravatar.com/x.png")
        == "https://gravatar.com/x.png"
    )


def test_normalize_avatar_url_strips_app_serve_url():
    stored = "/v1/files/avatars/user/abc.png?token=x&expires=1"
    assert normalize_avatar_url(stored) == "avatars/user/abc.png"


def test_normalize_avatar_url_bare_key_unchanged():
    assert normalize_avatar_url("avatars/user/abc.png") == "avatars/user/abc.png"


def test_normalize_avatar_url_none():
    assert normalize_avatar_url(None) is None

"""Tests for URL signing and resolution in sheaf/files.py."""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from sheaf.config import settings
from sheaf.files import (
    EXTERNAL_IMAGE_HIDDEN,
    internal_key_owner,
    normalize_avatar_url,
    normalize_description_urls,
    owned_avatar_url,
    owned_description_urls,
    resolve_avatar_url,
    resolve_avatar_url_public,
    resolve_description_urls,
    resolve_description_urls_public,
    sign_cdn_url,
    sign_file_url,
    sign_public_file_url,
    verify_file_token,
    verify_public_file_token,
)
from sheaf.markdown_images import iter_markdown_images


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch):
    """Each test starts with known settings and restores on exit."""
    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    monkeypatch.setattr(settings, "s3_public_url", "")
    monkeypatch.setattr(settings, "image_serving", "signed")
    monkeypatch.setattr(settings, "file_signing_key", "")
    monkeypatch.setattr(settings, "file_url_expiry_seconds", 3600)
    monkeypatch.setattr(settings, "allow_external_images", True)


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


def test_public_file_token_is_domain_separated_from_regular_serve_token():
    key = "avatars/user/abc.png"
    public_url = sign_public_file_url(key)
    public_q = parse_qs(urlparse(public_url).query)
    regular_url = sign_file_url(key)
    regular_q = parse_qs(urlparse(regular_url).query)

    assert public_url.startswith(f"/v1/public/files/{key}?")
    assert verify_public_file_token(
        key, public_q["token"][0], public_q["expires"][0]
    )
    assert not verify_file_token(key, public_q["token"][0], public_q["expires"][0])
    assert not verify_public_file_token(
        key, regular_q["token"][0], regular_q["expires"][0]
    )


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


def test_normalize_avatar_url_strips_public_serve_url():
    stored = "/v1/public/files/avatars/user/abc.png?token=x&expires=1"
    assert normalize_avatar_url(stored) == "avatars/user/abc.png"


def test_normalize_avatar_url_bare_key_unchanged():
    assert normalize_avatar_url("avatars/user/abc.png") == "avatars/user/abc.png"


def test_normalize_avatar_url_none():
    assert normalize_avatar_url(None) is None


def test_normalize_avatar_url_external_dropped_when_disabled(monkeypatch):
    """When the instance disables external images, external avatar URLs are
    dropped to None rather than silently stored."""
    monkeypatch.setattr(settings, "allow_external_images", False)
    assert normalize_avatar_url("https://gravatar.com/x.png") is None


def test_normalize_avatar_url_malformed_network_url_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "allow_external_images", False)

    assert normalize_avatar_url("http://[") is None


def test_normalize_avatar_url_bare_key_survives_when_external_disabled(monkeypatch):
    """Toggling off external images must not break hosted avatars."""
    monkeypatch.setattr(settings, "allow_external_images", False)
    assert normalize_avatar_url("avatars/user/abc.png") == "avatars/user/abc.png"


def test_normalize_description_urls_strips_external_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "allow_external_images", False)
    result = normalize_description_urls(
        "Hi ![pic](https://example.com/a.png) there"
    )
    assert "example.com" not in result
    assert result.startswith("Hi ")
    assert result.endswith(" there")


@pytest.mark.parametrize(
    "url",
    [
        "HTTPS://tracker.example/a.png",
        "hTtPs://tracker.example/a.png",
        "//tracker.example/a.png",
    ],
)
def test_normalize_description_urls_cannot_bypass_external_policy_by_url_form(
    monkeypatch, url
):
    monkeypatch.setattr(settings, "allow_external_images", False)

    result = normalize_description_urls(f"before ![pixel]({url}) after")

    assert "tracker.example" not in result
    assert result == "before  after"


def test_normalize_description_urls_treats_data_scheme_as_non_network(monkeypatch):
    monkeypatch.setattr(settings, "allow_external_images", False)
    text = "![dot](DATA:image/png;base64,iVBORw0KGgo=)"

    assert normalize_description_urls(text) == text


def test_normalize_description_urls_preserves_hosted_when_external_disabled(monkeypatch):
    monkeypatch.setattr(settings, "allow_external_images", False)
    result = normalize_description_urls(
        "See ![pic](/v1/files/avatars/u/a.png)"
    )
    assert "/v1/files/avatars/u/a.png" in result


def test_normalize_description_urls_canonicalises_signed_hosted_url(monkeypatch):
    """Signed URLs round-trip back through normalize as bare /v1/files/ form."""
    result = normalize_description_urls(
        "See ![pic](/v1/files/members/m/a.png?token=xxx&expires=123)"
    )
    assert result == "See ![pic](/v1/files/members/m/a.png)"


def test_normalize_description_urls_canonicalises_cdn_url(monkeypatch):
    """CDN-form URLs are recognised as ours and stored as /v1/files/{key}.

    Without this, a bio rendered with a CDN URL round-trips through the client
    and comes back looking external — which either strips it (policy off) or
    persists a stale token (policy on).
    """
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://cdn.example.com")
    result = normalize_description_urls(
        "See ![pic](https://cdn.example.com/members/m/a.png?token=xxx&expires=1)"
    )
    assert result == "See ![pic](/v1/files/members/m/a.png)"


def test_normalize_description_urls_cdn_preserved_even_when_external_disabled(monkeypatch):
    """The reported bug: hosted bio images must survive a save when
    ALLOW_EXTERNAL_IMAGES=false, even under the CDN paradigm."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://cdn.example.com")
    monkeypatch.setattr(settings, "allow_external_images", False)
    result = normalize_description_urls(
        "Portrait ![pic](https://cdn.example.com/members/m/a.png?token=old&expires=1)"
    )
    assert "/v1/files/members/m/a.png" in result
    assert "cdn.example.com" not in result


def test_resolve_description_urls_signs_hosted(monkeypatch):
    result = resolve_description_urls("![pic](/v1/files/members/m/a.png)")
    assert "/v1/files/members/m/a.png" in result
    assert "token=" in result
    assert "expires=" in result


def test_resolve_description_urls_resigns_legacy_cdn_row(monkeypatch):
    """Rows written before the CDN-recognition fix contain full CDN URLs with
    stale tokens; resolve re-signs them so the client gets a fresh URL."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://cdn.example.com")
    result = resolve_description_urls(
        "![pic](https://cdn.example.com/members/m/a.png?token=STALE&expires=1)"
    )
    assert "STALE" not in result
    assert "cdn.example.com/members/m/a.png" in result
    assert "token=" in result


def test_resolve_description_urls_leaves_external_untouched(monkeypatch):
    result = resolve_description_urls("![avatar](https://gravatar.com/x.png)")
    assert result == "![avatar](https://gravatar.com/x.png)"


# ---------------------------------------------------------------------------
# Public surface: an external image would make an anonymous visitor's browser
# announce itself to a host the profile owner chose, so it never ships.


def test_resolve_description_urls_public_signs_hosted():
    result = resolve_description_urls_public(
        f"![pic](/v1/files/bios/{_OWNER}/a.png)", _OWNER
    )
    assert f"/v1/public/files/bios/{_OWNER}/a.png" in result
    assert "token=" in result
    assert "expires=" in result


def test_resolve_description_urls_public_hides_external():
    result = resolve_description_urls_public(
        "![evil](https://tracker.example/p.png)", _OWNER
    )
    assert result == f"![evil]({EXTERNAL_IMAGE_HIDDEN})"
    assert "tracker.example" not in result


@pytest.mark.parametrize(
    ("text", "expected_alt"),
    [
        ("![foo [bar]](https://tracker.example/nested.png)", "foo [bar]"),
        (r"![foo \] bar](https://tracker.example/escaped.png)", r"foo \] bar"),
    ],
)
def test_resolve_description_urls_public_uses_commonmark_image_grammar(
    text, expected_alt
):
    result = resolve_description_urls_public(text, _OWNER)

    assert "tracker.example" not in result
    assert EXTERNAL_IMAGE_HIDDEN in result
    rewritten = list(iter_markdown_images(result))
    assert len(rewritten) == 1
    assert rewritten[0].url == EXTERNAL_IMAGE_HIDDEN
    assert rewritten[0].alt == expected_alt


def test_resolve_description_urls_public_keeps_data_uri():
    for scheme in ("data", "DATA"):
        text = f"![dot]({scheme}:image/png;base64,iVBORw0KGgo=)"
        assert resolve_description_urls_public(text, _OWNER) == text


def test_resolve_description_urls_public_mixed_content():
    text = (
        f"Intro ![mine](/v1/files/bios/{_OWNER}/a.png) middle "
        "![theirs](https://tracker.example/p.png) end [a link](https://ok.example)"
    )
    result = resolve_description_urls_public(text, _OWNER)
    assert "tracker.example" not in result
    assert result.count(EXTERNAL_IMAGE_HIDDEN) == 1
    assert f"/v1/public/files/bios/{_OWNER}/a.png?token=" in result
    # Prose and ordinary links are not image fetches; they stay.
    assert "Intro " in result and "[a link](https://ok.example)" in result


def test_resolve_description_urls_public_hides_reference_images():
    """Every external reference-image form is hidden."""
    text = (
        "![full][t] ![collapsed][] ![shortcut]\n\n"
        "[t]: https://tracker.example/1.png\n"
        "[collapsed]: https://tracker.example/2.png\n"
        "[shortcut]: https://tracker.example/3.png\n"
    )
    result = resolve_description_urls_public(text, _OWNER)
    assert result.count(f"]({EXTERNAL_IMAGE_HIDDEN})") == 3


def test_resolve_description_urls_public_serves_hosted_reference_same_origin():
    text = f"![portrait][photo]\n\n[photo]: /v1/files/bios/{_OWNER}/photo.png"

    result = resolve_description_urls_public(text, _OWNER)

    assert f"/v1/public/files/bios/{_OWNER}/photo.png?token=" in result


def test_resolve_description_urls_public_leaves_undefined_shortcut_alone():
    """Without a definition, ![like this] is prose, not an image."""
    text = "Filed under ![not an image] in the notes."
    assert resolve_description_urls_public(text, _OWNER) == text


def test_resolve_description_urls_public_none():
    assert resolve_description_urls_public(None, _OWNER) is None


def test_resolve_avatar_url_public_signs_internal():
    result = resolve_avatar_url_public(f"avatars/{_OWNER}/abc.png", _OWNER)
    assert result.startswith(f"/v1/public/files/avatars/{_OWNER}/abc.png?token=")


def test_resolve_avatar_url_public_signs_legacy_cdn_row(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://cdn.example.com")
    result = resolve_avatar_url_public(
        f"https://cdn.example.com/avatars/{_OWNER}/abc.png?token=STALE&expires=1",
        _OWNER,
    )
    assert result.startswith(f"/v1/public/files/avatars/{_OWNER}/abc.png?token=")
    assert "STALE" not in result


@pytest.mark.parametrize(
    ("storage_backend", "image_serving", "s3_public_url"),
    [
        ("filesystem", "signed", ""),
        ("s3", "signed", ""),
        ("s3", "signed", "https://images.example.com"),
        ("s3", "unsigned", "https://images.example.com"),
    ],
)
async def test_public_file_route_serves_same_origin_in_every_storage_mode(
    monkeypatch, storage_backend, image_serving, s3_public_url
):
    from sheaf.api.v1 import files as files_api

    class StubStorage:
        async def get(self, key):
            assert key == "avatars/user/abc.png"
            return b"image-bytes"

    monkeypatch.setattr(settings, "storage_backend", storage_backend)
    monkeypatch.setattr(settings, "image_serving", image_serving)
    monkeypatch.setattr(settings, "s3_public_url", s3_public_url)
    monkeypatch.setattr(files_api, "get_storage", lambda: StubStorage())
    signed = sign_public_file_url("avatars/user/abc.png")
    raw_query = urlparse(signed).query
    query = parse_qs(raw_query)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": urlparse(signed).path,
            "raw_path": urlparse(signed).path.encode(),
            "query_string": raw_query.encode(),
            "headers": [],
        }
    )

    response = await files_api.serve_public_file(
        "avatars/user/abc.png",
        request,
        token=query["token"][0],
        expires=query["expires"][0],
    )

    assert response.status_code == 200
    assert response.body == b"image-bytes"
    assert response.media_type == "image/png"
    assert response.headers["cache-control"].startswith("public, max-age=")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "location" not in response.headers


@pytest.mark.parametrize(
    "mutate_query",
    [
        lambda token, expires: f"token={token}&expires={expires}&bust=1",
        lambda token, expires: f"token={token}&token={token}&expires={expires}",
        lambda token, expires: f"expires={expires}&token={token}",
        lambda token, expires: (
            f"token=%{ord(token[0]):02X}{token[1:]}&expires={expires}"
        ),
    ],
)
async def test_public_file_route_rejects_cache_key_aliases(monkeypatch, mutate_query):
    from sheaf.api.v1 import files as files_api

    signed = sign_public_file_url("avatars/user/abc.png")
    parsed = urlparse(signed)
    query = parse_qs(parsed.query)
    token = query["token"][0]
    expires = query["expires"][0]
    raw_query = mutate_query(token, expires)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": parsed.path,
            "raw_path": parsed.path.encode(),
            "query_string": raw_query.encode(),
            "headers": [],
        }
    )
    storage_called = False

    def _storage():
        nonlocal storage_called
        storage_called = True
        raise AssertionError("noncanonical capability reached storage")

    monkeypatch.setattr(files_api, "get_storage", _storage)
    with pytest.raises(HTTPException) as exc:
        await files_api.serve_public_file(
            "avatars/user/abc.png",
            request,
            token=token,
            expires=expires,
        )

    assert exc.value.status_code == 403
    assert not storage_called


async def test_public_file_route_rejects_percent_encoded_path_alias(monkeypatch):
    from sheaf.api.v1 import files as files_api

    signed = sign_public_file_url("avatars/user/abc.png")
    parsed = urlparse(signed)
    query = parse_qs(parsed.query)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": parsed.path,
            "raw_path": parsed.path.replace("/avatars", "/%61vatars").encode(),
            "query_string": parsed.query.encode(),
            "headers": [],
        }
    )
    storage_called = False

    def _storage():
        nonlocal storage_called
        storage_called = True
        raise AssertionError("noncanonical capability reached storage")

    monkeypatch.setattr(files_api, "get_storage", _storage)
    with pytest.raises(HTTPException) as exc:
        await files_api.serve_public_file(
            "avatars/user/abc.png",
            request,
            token=query["token"][0],
            expires=query["expires"][0],
        )

    assert exc.value.status_code == 403
    assert not storage_called


def test_resolve_avatar_url_public_drops_external():
    assert resolve_avatar_url_public("https://gravatar.com/x.png", _OWNER) is None


def test_resolve_avatar_url_public_drops_data_uri():
    assert (
        resolve_avatar_url_public("data:image/png;base64,iVBORw0KGgo=", _OWNER)
        is None
    )


def test_resolve_avatar_url_public_none():
    assert resolve_avatar_url_public(None, _OWNER) is None


# ---------------------------------------------------------------------------
# Ownership binding: a caller can't persist another account's storage key
# (which would be re-signed into a live serve URL on read).

_OWNER = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"


def test_internal_key_owner_extracts_user_segment():
    assert internal_key_owner(f"avatars/{_OWNER}/abc.png") == _OWNER
    assert internal_key_owner(f"bios/{_OTHER}/x.png") == _OTHER
    assert internal_key_owner(f"banners/{_OWNER}/y.webp") == _OWNER


def test_internal_key_owner_rejects_non_media_prefix():
    # An exports/ key (or anything outside the upload prefixes) has no owner.
    assert internal_key_owner(f"exports/{_OWNER}/dump.zip") is None
    assert internal_key_owner("garbage") is None


def test_owned_avatar_url_keeps_own_key():
    key = f"avatars/{_OWNER}/abc.png"
    assert owned_avatar_url(key, _OWNER) == key


def test_owned_avatar_url_drops_foreign_key():
    # The core exploit: storing someone else's key must be refused.
    assert owned_avatar_url(f"avatars/{_OTHER}/abc.png", _OWNER) is None


def test_owned_avatar_url_drops_non_media_prefix_key():
    assert owned_avatar_url(f"exports/{_OWNER}/dump.zip", _OWNER) is None


def test_owned_avatar_url_passes_external_and_none():
    assert owned_avatar_url("https://gravatar.com/x.png", _OWNER) == (
        "https://gravatar.com/x.png"
    )
    assert owned_avatar_url(None, _OWNER) is None


def test_owned_avatar_url_accepts_uuid_owner_object():
    import uuid

    owner = uuid.UUID(_OWNER)
    key = f"avatars/{_OWNER}/abc.png"
    assert owned_avatar_url(key, owner) == key
    assert owned_avatar_url(f"avatars/{_OTHER}/abc.png", owner) is None


def test_owned_description_urls_drops_foreign_embed():
    text = f"before ![pic](/v1/files/bios/{_OTHER}/a.png) after"
    result = owned_description_urls(text, _OWNER)
    assert _OTHER not in result
    assert "before " in result and " after" in result


def test_owned_description_urls_keeps_own_embed():
    text = f"![pic](/v1/files/bios/{_OWNER}/a.png)"
    assert owned_description_urls(text, _OWNER) == text


def test_owned_description_urls_keeps_external_embed():
    text = "![pic](https://gravatar.com/x.png)"
    assert owned_description_urls(text, _OWNER) == text


def test_owned_description_urls_mixed_keeps_own_drops_foreign():
    text = (
        f"![mine](/v1/files/avatars/{_OWNER}/m.png) "
        f"![theirs](/v1/files/avatars/{_OTHER}/t.png)"
    )
    result = owned_description_urls(text, _OWNER)
    assert f"avatars/{_OWNER}/m.png" in result
    assert _OTHER not in result


# ---------------------------------------------------------------------------
# The signer is the last line: the PUBLIC resolvers re-check ownership rather
# than trusting that a write handler ran `owned_*` on the way in. Write-path
# guards get added one endpoint at a time, importers are written by other
# people, and rows already in the database predate all of it - so a stale
# foreign key in stored text has to render as hidden without anyone sweeping
# the data first.


def test_public_description_resolver_refuses_to_sign_foreign_key():
    text = f"![theirs](/v1/files/bios/{_OTHER}/a.png)"
    result = resolve_description_urls_public(text, _OWNER)
    # Treated exactly like an external ref: hidden, never signed.
    assert result == f"![theirs]({EXTERNAL_IMAGE_HIDDEN})"
    assert _OTHER not in result
    assert "token=" not in result


def test_public_description_resolver_signs_own_key_beside_foreign_one():
    text = (
        f"![mine](/v1/files/bios/{_OWNER}/m.png) "
        f"![theirs](/v1/files/bios/{_OTHER}/t.png)"
    )
    result = resolve_description_urls_public(text, _OWNER)
    assert f"/v1/public/files/bios/{_OWNER}/m.png?token=" in result
    assert _OTHER not in result
    assert result.count(EXTERNAL_IMAGE_HIDDEN) == 1


def test_public_description_resolver_hides_non_media_prefix_key():
    """A key outside the upload prefixes has no owner segment to match, and the
    serve route would refuse it anyway - so it is hidden, not signed."""
    text = f"![dump](/v1/files/exports/{_OWNER}/dump.png)"
    result = resolve_description_urls_public(text, _OWNER)
    assert result == f"![dump]({EXTERNAL_IMAGE_HIDDEN})"


def test_public_avatar_resolver_refuses_to_sign_foreign_key():
    assert resolve_avatar_url_public(f"avatars/{_OTHER}/abc.png", _OWNER) is None


def test_public_avatar_resolver_refuses_foreign_legacy_cdn_row(monkeypatch):
    """The CDN form of a foreign key is the same capability, one hostname on."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://cdn.example.com")
    assert (
        resolve_avatar_url_public(
            f"https://cdn.example.com/avatars/{_OTHER}/abc.png", _OWNER
        )
        is None
    )


def test_public_resolvers_accept_uuid_owner_object():
    """Call sites pass `System.user_id`, which is a UUID, not a string."""
    import uuid

    owner = uuid.UUID(_OWNER)
    assert resolve_avatar_url_public(
        f"avatars/{_OWNER}/abc.png", owner
    ).startswith("/v1/public/files/")
    assert resolve_avatar_url_public(f"avatars/{_OTHER}/abc.png", owner) is None


# ---------------------------------------------------------------------------
# Reference-style images face the same write-path policy as inline ones
# ---------------------------------------------------------------------------


def test_reference_image_stripped_when_external_disabled(monkeypatch):
    """`![alt][ref]` must not smuggle an external image past the instance
    policy: the image goes, the definition stays, a link sharing it works."""
    monkeypatch.setattr(settings, "allow_external_images", False)
    text = (
        "Look ![pixel][t] here, [click me][t] though.\n\n"
        "[t]: https://tracker.example/p.png"
    )
    result = normalize_description_urls(text)
    assert "![pixel]" not in result
    assert "[click me][t]" in result
    assert "[t]: https://tracker.example/p.png" in result


def test_reference_image_expanded_and_kept_when_allowed(monkeypatch):
    monkeypatch.setattr(settings, "allow_external_images", True)
    text = "![art][gallery]\n\n[gallery]: https://images.example/a.png"
    result = normalize_description_urls(text)
    assert result == text


def test_reference_image_cannot_bypass_url_safety(monkeypatch):
    """Even with externals allowed, a reference image's target faces the same
    validation as an inline one - http and internal hosts are stripped."""
    monkeypatch.setattr(settings, "allow_external_images", True)
    for bad in ("http://plain.example/x.png", "https://10.0.0.8/x.png"):
        text = f"![x][r]\n\n[r]: {bad}"
        result = normalize_description_urls(text)
        assert "![x]" not in result, bad


def test_reference_image_internal_key_canonicalised():
    text = "![mine][m]\n\n[m]: /v1/files/bios/u/f.png?token=stale"
    result = normalize_description_urls(text)
    assert "![mine](/v1/files/bios/u/f.png)" in result


def test_collapsed_and_shortcut_reference_images(monkeypatch):
    monkeypatch.setattr(settings, "allow_external_images", False)
    text = (
        "![Tracker][] and ![tracker]\n\n"
        "[tracker]: https://tracker.example/p.png"
    )
    result = normalize_description_urls(text)
    assert "![Tracker]" not in result
    assert "![tracker]" not in result


def test_reference_image_without_definition_left_alone(monkeypatch):
    """No definition means markdown renders literal text - nothing fetches,
    nothing to police."""
    monkeypatch.setattr(settings, "allow_external_images", False)
    text = "just ![a cat][nope] talking"
    assert normalize_description_urls(text) == text


def test_image_like_examples_in_code_are_not_normalized(monkeypatch):
    monkeypatch.setattr(settings, "allow_external_images", False)
    text = (
        "Inline `![example](https://tracker.example/inline.png)`\n\n"
        "```markdown\n![example](https://tracker.example/fenced.png)\n```\n\n"
        "    ![example](https://tracker.example/indented.png)\n"
    )

    assert normalize_description_urls(text) == text
    assert resolve_description_urls_public(text, _OWNER) == text


def test_unmatched_backticks_cannot_hide_image_in_later_paragraph(monkeypatch):
    """Inline parsing is block-scoped; backticks cannot span blank lines."""
    monkeypatch.setattr(settings, "allow_external_images", False)
    text = (
        "`open\n\n"
        "![leak](https://tracker.example/cross-paragraph.png)\n\n"
        "`close"
    )

    normalized = normalize_description_urls(text)
    public = resolve_description_urls_public(text, _OWNER)

    assert normalized == "`open\n\n\n\n`close"
    assert "tracker.example" not in public
    assert EXTERNAL_IMAGE_HIDDEN in public
    assert public.startswith("`open\n\n") and public.endswith("\n\n`close")


@pytest.mark.parametrize("line_ending", ["\r", "\r\n"])
def test_markdown_image_offsets_support_all_commonmark_line_endings(line_ending):
    text = f"![first{line_ending}second](https://tracker.example/image.png)"

    images = list(iter_markdown_images(text))

    assert len(images) == 1
    assert text[images[0].start : images[0].end] == text
    assert images[0].url == "https://tracker.example/image.png"


def test_normalize_avatar_url_drops_data_uri():
    """A data: avatar is not a storage key, so it must not survive as one."""
    assert normalize_avatar_url("data:image/png;base64,iVBORw0KGgo=") is None
    assert normalize_avatar_url("DATA:image/png;base64,iVBORw0KGgo=") is None

def test_resolve_description_urls_leaves_data_uri_alone():
    """A data: URI carries its own bytes; it is not a bare storage key."""
    text = "![dot](data:image/png;base64,iVBORw0KGgo=)"
    assert resolve_description_urls(text) == text

@pytest.mark.parametrize(
    ("text", "expected_alt"),
    [
        ("![foo [bar]](/v1/files/bios/u/nested.png)", "foo [bar]"),
        (r"![foo \] bar](/v1/files/bios/u/escaped.png)", r"foo \] bar"),
    ],
)
def test_resolve_description_urls_uses_commonmark_image_grammar(text, expected_alt):
    """Alt text with nested or escaped brackets survives the rewrite."""
    result = resolve_description_urls(text)

    rewritten = list(iter_markdown_images(result))
    assert len(rewritten) == 1
    assert rewritten[0].alt == expected_alt
    assert "token=" in rewritten[0].url

def test_resolve_description_urls_signs_hosted_reference_image():
    """Reference syntax resolves to the same signed URL as the inline form."""
    text = "![portrait][photo]\n\n[photo]: /v1/files/bios/u/photo.png"

    result = resolve_description_urls(text)

    assert "![portrait](/v1/files/bios/u/photo.png?token=" in result

def test_resolve_description_urls_leaves_code_examples_alone():
    """Code spans and code blocks are prose, not image fetches."""
    text = (
        "Inline `![example](/v1/files/bios/u/a.png)`\n\n"
        "```markdown\n![example](/v1/files/bios/u/b.png)\n```\n\n"
        "    ![example](/v1/files/bios/u/c.png)\n"
    )

    assert resolve_description_urls(text) == text


# ---------------------------------------------------------------------------
# Ownership binding: a caller can't persist another account's storage key
# (which would be re-signed into a live serve URL on read).

_OWNER = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"

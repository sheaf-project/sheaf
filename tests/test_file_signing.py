"""Tests for URL signing and resolution in sheaf/files.py."""

from urllib.parse import parse_qs, urlparse

import pytest

from sheaf.config import settings
from sheaf.files import (
    internal_key_owner,
    normalize_avatar_url,
    normalize_description_urls,
    owned_avatar_url,
    owned_description_urls,
    resolve_avatar_url,
    resolve_description_urls,
    sign_cdn_url,
    sign_file_url,
    verify_file_token,
)
from sheaf.markdown_images import iter_markdown_images

# Two account ids. Storage keys are `{prefix}/{user_id}/{uuid}.{ext}`, so the
# second segment is what every ownership check keys off.
_OWNER = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"


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


def test_resolve_avatar_url_s3_cdn_signed_routes_through_cdn(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    monkeypatch.setattr(settings, "image_serving", "signed")

    resolved = resolve_avatar_url(f"avatars/{_OWNER}/abc.png", _OWNER)
    assert resolved.startswith(
        f"https://images.example.com/avatars/{_OWNER}/abc.png?token="
    )
    assert "expires=" in resolved


def test_resolve_avatar_url_s3_cdn_unsigned_is_bare(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    monkeypatch.setattr(settings, "image_serving", "unsigned")

    assert (
        resolve_avatar_url(f"avatars/{_OWNER}/abc.png", _OWNER)
        == f"https://images.example.com/avatars/{_OWNER}/abc.png"
    )


def test_resolve_avatar_url_filesystem_signed_uses_app_serve(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    monkeypatch.setattr(settings, "image_serving", "signed")

    resolved = resolve_avatar_url(f"avatars/{_OWNER}/abc.png", _OWNER)
    assert resolved.startswith(f"/v1/files/avatars/{_OWNER}/abc.png?token=")
    # And matches what sign_file_url would produce
    assert resolved == sign_file_url(f"avatars/{_OWNER}/abc.png")


def test_resolve_avatar_url_s3_without_public_url_falls_back_to_app_serve(monkeypatch):
    """S3 backend but no CDN configured → serve via the app, same as filesystem."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "")
    monkeypatch.setattr(settings, "image_serving", "signed")

    resolved = resolve_avatar_url(f"avatars/{_OWNER}/abc.png", _OWNER)
    assert resolved.startswith(f"/v1/files/avatars/{_OWNER}/abc.png?token=")


def test_resolve_avatar_url_external_url_passthrough():
    assert resolve_avatar_url("https://gravatar.com/x.png", _OWNER) == (
        "https://gravatar.com/x.png"
    )


def test_resolve_avatar_url_none():
    assert resolve_avatar_url(None, _OWNER) is None


def test_resolve_avatar_url_legacy_full_cdn_url_gets_signed(monkeypatch):
    """DB row written before CDN-aware code has the full CDN URL, no token.
    It should still be recognised and signed on read."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    monkeypatch.setattr(settings, "image_serving", "signed")

    stored = f"https://images.example.com/avatars/{_OWNER}/abc.png"
    resolved = resolve_avatar_url(stored, _OWNER)
    assert resolved.startswith(
        f"https://images.example.com/avatars/{_OWNER}/abc.png?token="
    )
    assert "expires=" in resolved


def test_resolve_avatar_url_stored_signed_cdn_url_resigns(monkeypatch):
    """DB row containing a stale signed CDN URL should drop the old token
    and get a fresh one, not be returned verbatim."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    monkeypatch.setattr(settings, "image_serving", "signed")

    stored = (
        f"https://images.example.com/avatars/{_OWNER}/abc.png"
        "?token=deadbeef&expires=1"
    )
    resolved = resolve_avatar_url(stored, _OWNER)
    assert "token=deadbeef" not in resolved
    assert "expires=1&" not in resolved and not resolved.endswith("expires=1")
    # And is in fact a valid signed URL
    q = parse_qs(urlparse(resolved).query)
    assert verify_file_token(
        f"avatars/{_OWNER}/abc.png", q["token"][0], q["expires"][0]
    )


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


def test_normalize_avatar_url_external_dropped_when_disabled(monkeypatch):
    """When the instance disables external images, external avatar URLs are
    dropped to None rather than silently stored."""
    monkeypatch.setattr(settings, "allow_external_images", False)
    assert normalize_avatar_url("https://gravatar.com/x.png") is None


def test_normalize_avatar_url_malformed_network_url_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "allow_external_images", False)

    assert normalize_avatar_url("http://[") is None


def test_normalize_avatar_url_drops_data_uri():
    """A data: avatar is not a storage key, so it must not survive as one."""
    assert normalize_avatar_url("data:image/png;base64,iVBORw0KGgo=") is None
    assert normalize_avatar_url("DATA:image/png;base64,iVBORw0KGgo=") is None


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
    result = resolve_description_urls(
        f"![pic](/v1/files/bios/{_OWNER}/a.png)", _OWNER
    )
    assert f"/v1/files/bios/{_OWNER}/a.png" in result
    assert "token=" in result
    assert "expires=" in result


def test_resolve_description_urls_resigns_legacy_cdn_row(monkeypatch):
    """Rows written before the CDN-recognition fix contain full CDN URLs with
    stale tokens; resolve re-signs them so the client gets a fresh URL."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://cdn.example.com")
    result = resolve_description_urls(
        f"![pic](https://cdn.example.com/bios/{_OWNER}/a.png?token=STALE&expires=1)",
        _OWNER,
    )
    assert "STALE" not in result
    assert f"cdn.example.com/bios/{_OWNER}/a.png" in result
    assert "token=" in result


def test_resolve_description_urls_leaves_external_untouched(monkeypatch):
    result = resolve_description_urls(
        "![avatar](https://gravatar.com/x.png)", _OWNER
    )
    assert result == "![avatar](https://gravatar.com/x.png)"


def test_resolve_description_urls_leaves_data_uri_alone():
    """A data: URI carries its own bytes; it is not a bare storage key."""
    text = "![dot](data:image/png;base64,iVBORw0KGgo=)"
    assert resolve_description_urls(text, _OWNER) == text


@pytest.mark.parametrize(
    ("text", "expected_alt"),
    [
        (f"![foo [bar]](/v1/files/bios/{_OWNER}/nested.png)", "foo [bar]"),
        (rf"![foo \] bar](/v1/files/bios/{_OWNER}/escaped.png)", r"foo \] bar"),
    ],
)
def test_resolve_description_urls_uses_commonmark_image_grammar(text, expected_alt):
    """Alt text with nested or escaped brackets survives the rewrite."""
    result = resolve_description_urls(text, _OWNER)

    rewritten = list(iter_markdown_images(result))
    assert len(rewritten) == 1
    assert rewritten[0].alt == expected_alt
    assert "token=" in rewritten[0].url


def test_resolve_description_urls_signs_hosted_reference_image():
    """Reference syntax resolves to the same signed URL as the inline form."""
    text = f"![portrait][photo]\n\n[photo]: /v1/files/bios/{_OWNER}/photo.png"

    result = resolve_description_urls(text, _OWNER)

    assert f"![portrait](/v1/files/bios/{_OWNER}/photo.png?token=" in result


def test_resolve_description_urls_leaves_code_examples_alone():
    """Code spans and code blocks are prose, not image fetches."""
    text = (
        f"Inline `![example](/v1/files/bios/{_OWNER}/a.png)`\n\n"
        f"```markdown\n![example](/v1/files/bios/{_OWNER}/b.png)\n```\n\n"
        f"    ![example](/v1/files/bios/{_OWNER}/c.png)\n"
    )

    assert resolve_description_urls(text, _OWNER) == text


# ---------------------------------------------------------------------------
# Ownership binding: a caller can't persist another account's storage key
# (which would be re-signed into a live serve URL on read).


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
# The signer is the backstop: it refuses to mint a capability for a key that
# is not the profile's, whatever wrote the row. Catches legacy rows and any
# writer that ever slips past the handler-side ownership filters.
# ---------------------------------------------------------------------------


def test_resolve_avatar_url_does_not_sign_foreign_key():
    stored = f"avatars/{_OTHER}/abc.png"

    resolved = resolve_avatar_url(stored, _OWNER)

    # Unsigned canonical serve path: 403s at the serve endpoint while signing
    # is on, and never carries a token that would actually fetch the bytes.
    assert resolved == f"/v1/files/avatars/{_OTHER}/abc.png"
    assert "token=" not in resolved


def test_resolve_avatar_url_does_not_sign_foreign_cdn_url(monkeypatch):
    """The CDN form is the sneaky one: it looks like an ordinary external
    https URL, so the importer scheme checks wave it through."""
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_public_url", "https://images.example.com")
    monkeypatch.setattr(settings, "image_serving", "signed")

    stored = f"https://images.example.com/avatars/{_OTHER}/abc.png"

    resolved = resolve_avatar_url(stored, _OWNER)

    assert resolved == f"/v1/files/avatars/{_OTHER}/abc.png"
    assert "token=" not in resolved


def test_resolve_avatar_url_does_not_sign_ownerless_key():
    """A key outside the upload prefixes has no owner segment to match."""
    resolved = resolve_avatar_url(f"exports/{_OWNER}/dump.zip", _OWNER)

    assert resolved == f"/v1/files/exports/{_OWNER}/dump.zip"
    assert "token=" not in resolved


def test_resolve_avatar_url_accepts_uuid_owner_object():
    import uuid

    key = f"avatars/{_OWNER}/abc.png"
    assert "token=" in resolve_avatar_url(key, uuid.UUID(_OWNER))
    assert resolve_avatar_url(key, uuid.UUID(_OTHER)) == f"/v1/files/{key}"


def test_resolve_description_urls_does_not_sign_foreign_embed():
    """A bio row carrying somebody else's key - however it got there - must
    not come back as a working URL."""
    text = f"before ![pic](/v1/files/bios/{_OTHER}/a.png) after"

    result = resolve_description_urls(text, _OWNER)

    assert result == f"before ![pic](/v1/files/bios/{_OTHER}/a.png) after"
    assert "token=" not in result


def test_resolve_description_urls_mixed_signs_own_only():
    text = (
        f"![mine](/v1/files/bios/{_OWNER}/m.png) "
        f"![theirs](/v1/files/bios/{_OTHER}/t.png)"
    )

    result = resolve_description_urls(text, _OWNER)

    images = {img.alt: img.url for img in iter_markdown_images(result)}
    assert "token=" in images["mine"]
    assert images["theirs"] == f"/v1/files/bios/{_OTHER}/t.png"


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


def test_unmatched_backticks_cannot_hide_image_in_later_paragraph(monkeypatch):
    """Inline parsing is block-scoped; backticks cannot span blank lines."""
    monkeypatch.setattr(settings, "allow_external_images", False)
    text = (
        "`open\n\n"
        "![leak](https://tracker.example/cross-paragraph.png)\n\n"
        "`close"
    )

    assert normalize_description_urls(text) == "`open\n\n\n\n`close"


@pytest.mark.parametrize("line_ending", ["\r", "\r\n"])
def test_markdown_image_offsets_support_all_commonmark_line_endings(line_ending):
    text = f"![first{line_ending}second](https://tracker.example/image.png)"

    images = list(iter_markdown_images(text))

    assert len(images) == 1
    assert text[images[0].start : images[0].end] == text
    assert images[0].url == "https://tracker.example/image.png"

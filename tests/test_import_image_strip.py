"""Unit tests for sheaf.services.import_image_strip.

These run in-process — no server needed. They cover the helper logic
that decides what counts as an internal hosted image reference and
strips it from import payloads.
"""

from __future__ import annotations

from sheaf.services.import_image_strip import (
    is_internal_image_ref,
    strip_internal_avatar_url,
    strip_internal_image_keys,
    strip_internal_image_refs_md,
    strip_internal_image_refs_md_to_none,
)

# ---------------------------------------------------------------------------
# is_internal_image_ref
# ---------------------------------------------------------------------------

def test_internal_serve_path_is_internal():
    assert is_internal_image_ref(
        "/v1/files/avatars/00000000-0000-0000-0000-000000000001/abc.png"
    )


def test_bare_storage_key_is_internal():
    assert is_internal_image_ref(
        "avatars/00000000-0000-0000-0000-000000000001/abc.png"
    )


def test_external_https_url_is_not_internal():
    assert not is_internal_image_ref("https://gravatar.com/avatar/hash.png")


def test_external_http_url_is_not_internal():
    assert not is_internal_image_ref("http://example.com/avatar.png")


def test_none_is_not_internal():
    assert not is_internal_image_ref(None)


def test_empty_string_is_not_internal():
    assert not is_internal_image_ref("")


# ---------------------------------------------------------------------------
# strip_internal_avatar_url
# ---------------------------------------------------------------------------

def test_strip_avatar_clears_internal_key():
    assert strip_internal_avatar_url(
        "avatars/00000000-0000-0000-0000-000000000001/abc.png"
    ) is None


def test_strip_avatar_clears_internal_serve_path():
    assert strip_internal_avatar_url(
        "/v1/files/avatars/00000000-0000-0000-0000-000000000001/abc.png"
    ) is None


def test_strip_avatar_preserves_external_url():
    url = "https://gravatar.com/avatar/abc.png"
    assert strip_internal_avatar_url(url) == url


def test_strip_avatar_passes_none_through():
    assert strip_internal_avatar_url(None) is None


# ---------------------------------------------------------------------------
# strip_internal_image_refs_md
# ---------------------------------------------------------------------------

def test_strip_md_removes_internal_serve_embed():
    text = "Hi! ![pic](/v1/files/bios/00000000-0000-0000-0000-000000000001/a.png) bye"
    cleaned = strip_internal_image_refs_md(text)
    assert "v1/files" not in cleaned
    assert cleaned.startswith("Hi!")
    assert cleaned.endswith("bye")


def test_strip_md_removes_bare_internal_key_embed():
    text = "before ![alt](bios/00000000-0000-0000-0000-000000000001/k.png) after"
    cleaned = strip_internal_image_refs_md(text)
    assert "bios/" not in cleaned


def test_strip_md_preserves_external_embed():
    text = "look ![at](https://imgur.com/x.png) this"
    assert strip_internal_image_refs_md(text) == text


def test_strip_md_mixes_internal_and_external():
    text = (
        "external ![a](https://imgur.com/x.png) "
        "internal ![b](/v1/files/bios/00000000-0000-0000-0000-000000000001/k.png) "
        "both"
    )
    cleaned = strip_internal_image_refs_md(text)
    assert "imgur.com" in cleaned
    assert "v1/files" not in cleaned


def test_strip_md_none_passes_through():
    assert strip_internal_image_refs_md(None) is None


def test_strip_md_preserves_non_image_markdown():
    # Plain links shouldn't be touched; only ![alt](...) embeds get stripped.
    text = "see [the docs](https://docs.example.com) for more"
    assert strip_internal_image_refs_md(text) == text


# ---------------------------------------------------------------------------
# strip_internal_image_refs_md_to_none
# ---------------------------------------------------------------------------

def test_strip_md_to_none_returns_none_when_text_was_only_internal_embeds():
    text = "  ![a](/v1/files/bios/00000000-0000-0000-0000-000000000001/x.png)  "
    assert strip_internal_image_refs_md_to_none(text) is None


def test_strip_md_to_none_keeps_text_when_other_content_remains():
    text = "hello ![a](/v1/files/bios/00000000-0000-0000-0000-000000000001/x.png) world"
    out = strip_internal_image_refs_md_to_none(text)
    assert out is not None
    assert "hello" in out and "world" in out


# ---------------------------------------------------------------------------
# strip_internal_image_keys
# ---------------------------------------------------------------------------

def test_strip_keys_drops_internal_keys():
    keys = [
        "avatars/00000000-0000-0000-0000-000000000001/a.png",
        "bios/00000000-0000-0000-0000-000000000002/b.png",
    ]
    assert strip_internal_image_keys(keys) == []


def test_strip_keys_handles_empty_and_none():
    assert strip_internal_image_keys(None) == []
    assert strip_internal_image_keys([]) == []


# ---------------------------------------------------------------------------
# sanitize_external_avatar_url (shared importer policy gate)


def test_sanitize_keeps_plain_https():
    from sheaf.services.import_parsing import sanitize_external_avatar_url

    url = "https://cdn.example.com/avatar.png"
    assert sanitize_external_avatar_url(url) == url


def test_sanitize_rejects_non_http_schemes():
    from sheaf.services.import_parsing import sanitize_external_avatar_url

    for bad in (
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "ftp://example.com/a.png",
        "file:///etc/passwd",
        "//example.com/a.png",
    ):
        assert sanitize_external_avatar_url(bad) is None, bad


def test_sanitize_rejects_non_strings_and_empty():
    from sheaf.services.import_parsing import sanitize_external_avatar_url

    assert sanitize_external_avatar_url(None) is None
    assert sanitize_external_avatar_url("") is None
    assert sanitize_external_avatar_url(123) is None
    assert sanitize_external_avatar_url(["https://example.com/a.png"]) is None


def test_sanitize_truncates_to_column_length():
    from sheaf.services.import_parsing import sanitize_external_avatar_url

    url = "https://example.com/" + "a" * 600
    out = sanitize_external_avatar_url(url)
    assert out is not None and len(out) == 500


def test_strip_avatar_drops_weird_scheme_externals():
    """strip_internal_avatar_url routes surviving externals through the
    shared policy gate, so a re-import can't smuggle in a scheme the
    profile-write path would refuse."""
    assert strip_internal_avatar_url("javascript:alert(1)") is None

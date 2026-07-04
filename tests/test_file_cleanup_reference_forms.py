"""Regression tests for the 2026-07-03 orphan-cleanup over-deletion.

The cleanup job decides a blob is orphaned by comparing the uploaded-file key
against the set of *referenced* keys. A reference to an internal file can be
stored in three forms, and the bug was that cleanup only recognised one or two
of them, so a live reference stored in the unrecognised form looked orphaned
and the blob got reaped:

  1. app serve path        ``/v1/files/<key>``
  2. CDN-hostname URL       ``{s3_public_url}/<key>``  (legacy rows; may be
                            a *signed* URL carrying ``?token=...&expires=...``)
  3. bare storage key       ``avatars/<uid>/<uuid>.png``

Every referencing surface funnels through one of two primitives:

  * avatars + banners -> ``_key_from_avatar``
  * bios + journals + revisions -> ``extract_image_keys`` (the journal/revision
    ``image_keys`` columns are produced by this same function at write time and
    by the CDN backfill migration)

so exercising both primitives across all three forms covers the full
kind x form matrix. External URLs must resolve away (never mistaken for one of
our own keys).

Pure - no DB / storage stack.
"""

from __future__ import annotations

import pytest

from sheaf.config import settings
from sheaf.services.file_cleanup import _key_from_avatar
from sheaf.services.markdown import extract_image_keys

_CDN = "https://images.example.test"


@pytest.fixture(autouse=True)
def _cdn_configured(monkeypatch):
    """Point the resolver at a known CDN host so form #2 is exercised."""
    monkeypatch.setattr(settings, "s3_public_url", _CDN, raising=False)
    monkeypatch.setattr(settings, "allow_external_images", True, raising=False)


# --- avatar / banner primitive --------------------------------------------

AVATAR_KEY = "avatars/user-1/abc.png"


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param(f"/v1/files/{AVATAR_KEY}", id="form1-serve-path"),
        pytest.param(f"{_CDN}/{AVATAR_KEY}", id="form2-cdn-url"),
        pytest.param(
            f"{_CDN}/{AVATAR_KEY}?token=deadbeef&expires=9999999999",
            id="form2-cdn-url-signed",
        ),
        pytest.param(AVATAR_KEY, id="form3-bare-key"),
    ],
)
def test_avatar_forms_resolve_to_bare_key(stored):
    # A live avatar/banner in ANY internal form must resolve to its bare key,
    # so it lands in `referenced` and its blob is never treated as orphaned.
    assert _key_from_avatar(stored) == {AVATAR_KEY}


def test_external_avatar_is_not_claimed_as_ours():
    # An external avatar is not one of our uploaded files; it must not resolve
    # to a key (which could otherwise shadow/retain an unrelated blob).
    assert _key_from_avatar("https://gravatar.com/avatar/x.png") == set()


def test_none_and_empty_avatar():
    assert _key_from_avatar(None) == set()
    assert _key_from_avatar("") == set()


# --- markdown primitive (bio / journal / revision) ------------------------

BIO_KEY = "bios/user-1/pic.png"


@pytest.mark.parametrize(
    "url",
    [
        pytest.param(f"/v1/files/{BIO_KEY}", id="form1-serve-path"),
        pytest.param(f"{_CDN}/{BIO_KEY}", id="form2-cdn-url"),
        pytest.param(
            f"{_CDN}/{BIO_KEY}?token=deadbeef&expires=9999999999",
            id="form2-cdn-url-signed",
        ),
        pytest.param(BIO_KEY, id="form3-bare-key"),
    ],
)
def test_markdown_embed_forms_resolve_to_bare_key(url):
    body = f"intro text\n\n![a picture]({url})\n\nmore text"
    assert extract_image_keys(body) == [BIO_KEY]


def test_markdown_external_embed_dropped():
    body = "![gravatar](https://gravatar.com/avatar/x.png)"
    assert extract_image_keys(body) == []


def test_markdown_mixed_forms_all_captured():
    body = (
        f"![a](/v1/files/{BIO_KEY})\n"
        f"![b]({_CDN}/avatars/user-1/two.png?token=t&expires=1)\n"
        "![c](https://external.example/three.png)\n"
    )
    # Both internal forms captured, external dropped. Sorted, deduplicated.
    assert extract_image_keys(body) == [
        "avatars/user-1/two.png",
        BIO_KEY,
    ]


def test_markdown_empty_and_none():
    assert extract_image_keys(None) == []
    assert extract_image_keys("") == []
    assert extract_image_keys("no images here") == []


def test_selfhost_no_cdn_still_resolves_serve_and_bare_forms(monkeypatch):
    # With no CDN configured (the self-host default), form #2 doesn't exist, but
    # forms #1/#3 must still resolve and an arbitrary external host stays
    # external rather than being clawed in as one of our keys.
    monkeypatch.setattr(settings, "s3_public_url", "", raising=False)
    assert _key_from_avatar(f"/v1/files/{AVATAR_KEY}") == {AVATAR_KEY}
    assert _key_from_avatar(AVATAR_KEY) == {AVATAR_KEY}
    assert _key_from_avatar(f"{_CDN}/{AVATAR_KEY}") == set()
    assert extract_image_keys(f"![x](/v1/files/{BIO_KEY})") == [BIO_KEY]

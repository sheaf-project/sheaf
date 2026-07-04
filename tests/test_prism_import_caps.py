"""Pure unit tests for the Prism importer's business-cap preview pass and
its media parse-bomb caps.

These build a `ParsedPrism` directly from a decrypted-envelope dict, so
they exercise the `preview` measurement (`limit_warnings`) without any
crypto, database, or running server. The media-cap tests call
`_import_media_attachments` directly with `store_imported_image` stubbed,
so no image decode or storage actually happens. The full encrypted-envelope
path is covered by test_imports_prism_runner.py.
"""

from __future__ import annotations

import asyncio

from sheaf.config import settings
from sheaf.services import prism_import
from sheaf.services.import_media import ImportImageError
from sheaf.services.prism_crypto import DecryptedEnvelope
from sheaf.services.prism_import import ParsedPrism, preview


def _parsed(data: dict) -> ParsedPrism:
    # `header` is unused by the preview path (ParsedPrism only reads .json
    # and .media_blobs), so a None placeholder is fine for these pure tests.
    return ParsedPrism(
        envelope=DecryptedEnvelope(header=None, json=data, media_blobs={})
    )


def test_preview_flags_over_cap_member_name():
    """A member name past the 100-char cap shows up in limit_warnings so the
    user is warned before the import shortens it."""
    parsed = _parsed(
        {"headmates": [{"id": "m1", "name": "n" * 250}]}
    )
    summary = preview(parsed)
    assert summary.limit_warnings
    assert any("member name" in w for w in summary.limit_warnings)


def test_preview_flags_over_cap_birthday_and_group_name():
    """Birthday (the String(10) raw-write bug) and group name are both
    measured against their caps."""
    parsed = _parsed(
        {
            "headmates": [
                {"id": "m1", "name": "ok", "birthday": "1990-04-15T00:00:00Z"}
            ],
            "memberGroups": [{"id": "g1", "name": "g" * 200}],
        }
    )
    summary = preview(parsed)
    joined = " ".join(summary.limit_warnings)
    assert "member birthday" in joined
    assert "group name" in joined


def test_preview_clean_export_has_no_limit_warnings():
    parsed = _parsed(
        {
            "headmates": [
                {"id": "m1", "name": "Alex", "birthday": "04-15", "pronouns": "they"}
            ],
            "memberGroups": [{"id": "g1", "name": "Inner"}],
            "customFields": [{"id": "f1", "name": "Likes"}],
        }
    )
    summary = preview(parsed)
    assert summary.limit_warnings == []


def test_preview_surfaces_over_cap_fronts(monkeypatch):
    """A frontSessions count over the per-import cap is predicted in the
    preview so the user is warned before the job fails."""
    monkeypatch.setattr(settings, "import_max_fronts", 1)
    parsed = _parsed(
        {
            "headmates": [{"id": "m1", "name": "ok"}],
            "frontSessions": [{"id": "s1"}, {"id": "s2"}],
        }
    )
    summary = preview(parsed)
    assert any(
        "2 fronts" in w and "one job" in w for w in summary.limit_warnings
    ), summary.limit_warnings


def test_preview_over_cap_messages_counts_board_posts(monkeypatch):
    """Conversations and member board posts both become Message rows, so the
    messages cap prediction sums the two."""
    monkeypatch.setattr(settings, "import_max_messages", 2)
    parsed = _parsed(
        {
            "headmates": [{"id": "m1", "name": "ok"}],
            "messages": [{"id": "c1"}, {"id": "c2"}],
            "memberBoardPosts": [{"id": "b1"}],
        }
    )
    summary = preview(parsed)
    assert any(
        "3 messages" in w for w in summary.limit_warnings
    ), summary.limit_warnings


# ---------------------------------------------------------------------------
# Media parse-bomb caps (_import_media_attachments)
# ---------------------------------------------------------------------------


def _media_atts(n: int) -> tuple[list[dict], dict[str, bytes]]:
    atts = [{"mediaId": f"m{i}", "encryptionKeyB64": "key"} for i in range(n)]
    blobs = {f"m{i}": b"blob" for i in range(n)}
    return atts, blobs


def test_media_import_stops_at_image_cap(monkeypatch):
    """Once max_import_restored_images images have been stored, the loop stops
    rather than decoding every remaining attachment (each store runs a Pillow
    normalisation pass, so an unbounded loop is the parse bomb)."""
    monkeypatch.setattr(settings, "max_import_restored_images", 3)
    monkeypatch.setattr(prism_import, "user_can_upload_images", lambda user: True)
    monkeypatch.setattr(
        prism_import, "decrypt_media_blob", lambda blob, key: b"decoded"
    )
    calls = {"n": 0}

    async def fake_store(plaintext, *, db, user, purpose):
        calls["n"] += 1
        return object()

    monkeypatch.setattr(prism_import, "store_imported_image", fake_store)

    atts, blobs = _media_atts(10)
    warnings: list[str] = []
    imported = asyncio.run(
        prism_import._import_media_attachments(
            atts, blobs, None, object(), warnings
        )
    )
    assert imported == 3
    assert calls["n"] == 3  # stopped decoding once the cap was reached
    assert any("MAX_IMPORT_RESTORED_IMAGES" in w for w in warnings), warnings


def test_media_import_breaks_on_quota_full(monkeypatch):
    """On the first quota_full, image import stops entirely rather than running
    a normalise pass per remaining attachment on an already-full account."""
    monkeypatch.setattr(settings, "max_import_restored_images", 1000)
    monkeypatch.setattr(prism_import, "user_can_upload_images", lambda user: True)
    monkeypatch.setattr(
        prism_import, "decrypt_media_blob", lambda blob, key: b"decoded"
    )
    calls = {"n": 0}

    async def fake_store(plaintext, *, db, user, purpose):
        calls["n"] += 1
        raise ImportImageError("quota_full")

    monkeypatch.setattr(prism_import, "store_imported_image", fake_store)

    atts, blobs = _media_atts(10)
    warnings: list[str] = []
    imported = asyncio.run(
        prism_import._import_media_attachments(
            atts, blobs, None, object(), warnings
        )
    )
    assert imported == 0
    assert calls["n"] == 1  # broke on the first quota_full, didn't decode 10
    assert any("quota" in w.lower() for w in warnings), warnings

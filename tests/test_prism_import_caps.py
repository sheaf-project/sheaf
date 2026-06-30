"""Pure unit tests for the Prism importer's business-cap preview pass.

These build a `ParsedPrism` directly from a decrypted-envelope dict, so
they exercise the `preview` measurement (`limit_warnings`) without any
crypto, database, or running server. The full encrypted-envelope path is
covered by test_imports_prism_runner.py.
"""

from __future__ import annotations

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

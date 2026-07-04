"""Pure (no DB / no stack) test for the PluralSpace preview's business-cap
prediction.

Builds a tiny export zip whose data.json carries a few over-cap fields, runs
the synchronous `preview`, and asserts `limit_warnings` is populated and names
the right fields. This is the warn-before-import surface: the same caps the
real import clamps, measured over PluralSpace's own key names.
"""

from __future__ import annotations

import io
import json
import zipfile

from sheaf.services.pluralspace_import import parse_export, preview


def _build_zip(data: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": "1.1"}))
        zf.writestr("data.json", json.dumps(data))
    return buf.getvalue()


def test_preview_flags_over_cap_fields() -> None:
    data = {
        "system": {"name": "S" * 250},  # SYS_NAME cap 100
        "members": [
            {
                "id": "m1",
                "name": "N" * 250,  # M_NAME cap 100
                "display_name": "D" * 250,  # M_DISPLAY_NAME cap 100
                "pronouns": "P" * 250,  # M_PRONOUNS cap 100
                "role": ["R" * 80],  # TAG_NAME cap 50
            }
        ],
        "member_groups": [{"name": "G" * 250}],  # GROUP_NAME cap 100
        "custom_fields": [{"name": "F" * 250, "field_type": "text"}],  # CF_NAME 100
        "polls": [
            {
                "title": "Q" * 800,  # POLL_QUESTION cap 500
                "description": "X" * 3000,  # POLL_DESCRIPTION cap 2000
                "options": [{"id": "o1", "text": "O" * 400}],  # POLL_OPTION cap 200
            }
        ],
        "chat_channels": [
            {"name": "c1", "messages": [{"content": "B" * 6000}]}  # MESSAGE_BODY 5000
        ],
    }
    parsed = parse_export(_build_zip(data))
    summary = preview(parsed)

    assert summary.limit_warnings, "expected at least one cap warning"
    joined = " ".join(summary.limit_warnings).lower()
    for needle in (
        "system name",
        "member name",
        "member display name",
        "member pronouns",
        "tag name",
        "group name",
        "custom field name",
        "poll question",
        "poll description",
        "poll option",
        "message",
    ):
        assert needle in joined, f"missing cap warning for {needle!r}: {joined}"


def test_preview_no_warnings_when_within_caps() -> None:
    data = {
        "system": {"name": "Small System"},
        "members": [{"id": "m1", "name": "Alex", "pronouns": "they/them"}],
    }
    parsed = parse_export(_build_zip(data))
    summary = preview(parsed)
    assert summary.limit_warnings == []


def test_preview_surfaces_over_cap_messages(monkeypatch) -> None:
    """Chat rows (flattened across channels) over the per-import messages cap
    are predicted in the preview, ahead of the job's authoritative enforcement.
    """
    from sheaf.config import settings

    monkeypatch.setattr(settings, "import_max_messages", 2)
    data = {
        "system": {"name": "Sys"},
        "members": [{"id": "m1", "name": "Alex"}],
        "chat_channels": [
            {"name": "c1", "messages": [{"content": "a"}, {"content": "b"}]},
            {"name": "c2", "messages": [{"content": "c"}]},
        ],
    }
    parsed = parse_export(_build_zip(data))
    summary = preview(parsed)
    assert any(
        "3 messages" in w and "one job" in w for w in summary.limit_warnings
    ), summary.limit_warnings

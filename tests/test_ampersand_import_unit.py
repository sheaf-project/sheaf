"""Headless unit tests for the Ampersand importer's pure helpers.

No DB / docker stack needed - these exercise the parsing, preview, and
data-URI decode logic directly. The end-to-end runner behaviour lives in
`test_imports_ampersand_runner.py` (needs the test stack).
"""

from __future__ import annotations

import base64

from sheaf.services.ampersand_import import (
    _decode_data_uri,
    _normalize_color,
    _parse_iso,
    preview,
)

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
    b"\x08\x06\x00\x00\x00\xa9\xf1\x9e~\x00\x00\x00\x15IDATx\x9cc\xfc\xcf"
    b"\xc0\xf0\x9f\x01\t01\xa0\x01\xc2\x02\x00\x83\xd1\x02\x06\x02\x90\xef"
    b"X\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _data_uri(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def test_decode_data_uri_roundtrip():
    assert _decode_data_uri(_data_uri(_PNG)) == _PNG
    # A charset parameter before base64 is tolerated.
    assert _decode_data_uri("data:image/png;charset=utf-8;base64,"
                            + base64.b64encode(_PNG).decode()) == _PNG


def test_decode_data_uri_rejects_non_data_uri():
    assert _decode_data_uri("https://example.invalid/a.png") is None
    assert _decode_data_uri("data:image/png,notbase64") is None
    assert _decode_data_uri("data:image/png;base64,!!!not base64!!!") is None
    assert _decode_data_uri(None) is None
    assert _decode_data_uri(123) is None


def test_normalize_color():
    assert _normalize_color("#AABBCC") == "#aabbcc"
    assert _normalize_color("aabbcc") == "#aabbcc"
    assert _normalize_color("#abc") == "#aabbcc"
    # 8-hex ARGB drops the alpha byte.
    assert _normalize_color("#ff205c90") == "#205c90"
    assert _normalize_color("not a color") is None
    assert _normalize_color(None) is None


def test_parse_iso():
    dt = _parse_iso("2026-03-19T21:35:57.243Z")
    assert dt is not None
    assert dt.year == 2026 and dt.tzinfo is not None
    assert _parse_iso("garbage") is None
    assert _parse_iso(None) is None


def test_preview_counts():
    data = {
        "revision": {"count": 1, "humanReadable": "0.3.0"},
        "database": {
            "systems": [{"uuid": "s1", "name": "A"}, {"uuid": "s2", "name": "B", "parent": "s1"}],
            "members": [
                {"uuid": "m1", "name": "One", "system": "s1"},
                {"uuid": "m2", "name": "Away", "system": "s1", "isCustomFront": True},
            ],
            "frontingEntries": [
                {"uuid": "f1", "member": "m1", "startTime": "2026-01-01T00:00:00Z"}
            ],
            "tags": [{"uuid": "t1", "name": "tag", "type": "member"}],
            "customFields": [{"uuid": "c1", "name": "Species"}],
            "journalPosts": [{"uuid": "j1", "members": ["m1"], "body": "hi"}],
            "notes": [{"uuid": "n1", "title": "sticky", "content": "x"}],
            "boardMessages": [
                {
                    "uuid": "b1",
                    "members": ["m1"],
                    "body": "post",
                    "poll": {"multipleChoice": False, "entries": [{"choice": "a", "votes": []}]},
                }
            ],
            "reminders": [{"uuid": "r1", "title": "t", "message": "m", "trigger": "fronting"}],
            "assets": [{"uuid": "a1", "friendlyName": "img"}],
        },
    }
    s = preview(data)
    assert s.system_count == 2
    assert s.member_count == 1
    assert s.custom_front_count == 1
    assert s.front_history_count == 1
    assert s.tag_count == 1
    assert s.custom_field_count == 1
    assert s.journal_count == 1
    assert s.note_count == 1
    assert s.board_message_count == 1
    assert s.poll_count == 1
    assert s.reminder_count == 1
    assert s.asset_count == 1


def test_preview_handles_malformed_database():
    assert preview({}).member_count == 0
    assert preview({"database": None}).member_count == 0
    assert preview({"database": {"members": "nope"}}).member_count == 0

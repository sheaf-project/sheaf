"""Unit tests for Tupperbox import helpers.

These exercise the pure-Python normalisation helpers without a running
server or database. End-to-end coverage lives in test_tb_import.py.
"""

from sheaf.services.tb_import import (
    _clean_str,
    _normalize_birthday,
    _tupper_id,
    preview,
)


def test_normalize_birthday_takes_date_prefix_from_iso_timestamp():
    assert _normalize_birthday("2025-10-01T00:00:00.000Z") == "2025-10-01"
    assert _normalize_birthday("1990-04-15T00:00:00.000Z") == "1990-04-15"


def test_normalize_birthday_passes_through_plain_date():
    assert _normalize_birthday("1990-04-15") == "1990-04-15"


def test_normalize_birthday_drops_garbage():
    assert _normalize_birthday("totally not a date string") is None
    assert _normalize_birthday("2025/10/01") is None  # Wrong separator
    assert _normalize_birthday("") is None
    assert _normalize_birthday(None) is None


def test_clean_str_strips_and_nones_empties():
    assert _clean_str("  hello  ") == "hello"
    assert _clean_str("") is None
    assert _clean_str("   ") is None
    assert _clean_str(None) is None


def test_tupper_id_stringifies_numeric_ids():
    assert _tupper_id({"id": 12345}) == "12345"
    assert _tupper_id({"id": "abc"}) == "abc"
    assert _tupper_id({}) is None
    assert _tupper_id({"id": None}) is None


def test_preview_summarises_minimal_export():
    data = {
        "tuppers": [
            {"id": 1, "name": "Alpha"},
            {"id": 2, "name": "Beta"},
        ],
        "groups": [{"id": 10, "name": "Core"}],
    }
    summary = preview(data)
    assert summary.member_count == 2
    assert summary.group_count == 1
    ids = {m.id for m in summary.members}
    assert ids == {"1", "2"}


def test_preview_handles_missing_collections():
    summary = preview({})
    assert summary.member_count == 0
    assert summary.group_count == 0
    assert summary.members == []


def test_preview_skips_tuppers_without_id():
    """Defensive: a tupper without an id is unusable downstream so we drop it from preview."""
    data = {
        "tuppers": [
            {"id": 1, "name": "Alpha"},
            {"name": "Orphan"},
        ],
    }
    summary = preview(data)
    # member_count still reflects raw row count (matches PK behaviour).
    assert summary.member_count == 2
    # But the surfaced members list only contains rows with usable IDs.
    assert len(summary.members) == 1
    assert summary.members[0].id == "1"


def test_preview_warns_when_member_name_over_cap():
    """An over-cap tupper name surfaces a limit warning the user sees up front."""
    data = {
        "tuppers": [
            {"id": 1, "name": "x" * 200},  # M_NAME cap is 100
        ],
    }
    summary = preview(data)
    assert summary.limit_warnings
    assert any("member name" in w for w in summary.limit_warnings)


def test_preview_warns_for_nick_and_group_name_over_cap():
    """nick maps to display_name and group name each have their own cap."""
    data = {
        "tuppers": [
            {"id": 1, "name": "Alpha", "nick": "n" * 150},  # M_DISPLAY_NAME 100
        ],
        "groups": [{"id": 10, "name": "g" * 150}],  # GROUP_NAME 100
    }
    summary = preview(data)
    joined = " ".join(summary.limit_warnings)
    assert "member display name" in joined
    assert "group name" in joined


def test_preview_no_warnings_when_all_within_caps():
    data = {
        "tuppers": [{"id": 1, "name": "Alpha", "nick": "Al"}],
        "groups": [{"id": 10, "name": "Core"}],
    }
    summary = preview(data)
    assert summary.limit_warnings == []

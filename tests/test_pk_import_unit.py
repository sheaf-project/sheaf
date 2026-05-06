"""Unit tests for PluralKit import helpers.

These exercise the pure-Python normalisation and switch-conversion
helpers without needing a running server or database. The integration
tests in test_pk_import.py cover the full end-to-end path.
"""

from datetime import UTC, datetime

from sheaf.models.system import PrivacyLevel
from sheaf.services.pk_import import (
    _map_privacy,
    _normalize_birthday,
    _normalize_color,
    _parse_iso,
    preview,
)


def test_normalize_color_strips_hash_and_lowercases():
    assert _normalize_color("FF00AA") == "#ff00aa"
    assert _normalize_color("#abcdef") == "#abcdef"
    assert _normalize_color("  6c89bb  ") == "#6c89bb"


def test_normalize_color_rejects_invalid():
    assert _normalize_color("nope") is None
    assert _normalize_color("12345") is None  # Too short
    assert _normalize_color("zzzzzz") is None  # Non-hex
    assert _normalize_color("") is None
    assert _normalize_color(None) is None


def test_normalize_birthday_collapses_year_less_sentinel():
    assert _normalize_birthday("0004-07-20") == "07-20"


def test_normalize_birthday_passes_through_full_date():
    assert _normalize_birthday("1990-04-15") == "1990-04-15"


def test_normalize_birthday_passes_through_md_only():
    assert _normalize_birthday("04-15") == "04-15"


def test_normalize_birthday_truncates_garbage():
    assert _normalize_birthday("totally not a date string") == "totally no"
    assert _normalize_birthday("") is None
    assert _normalize_birthday(None) is None


def test_map_privacy_uses_visibility_when_present():
    assert _map_privacy({"visibility": "public"}) == PrivacyLevel.PUBLIC
    assert _map_privacy({"visibility": "private"}) == PrivacyLevel.PRIVATE


def test_map_privacy_falls_back_to_field_flags_when_no_visibility():
    # All fields public => member is public
    privacy = {
        "name_privacy": "public",
        "description_privacy": "public",
    }
    assert _map_privacy(privacy) == PrivacyLevel.PUBLIC

    # Mixed => private (most-restrictive wins)
    privacy = {
        "name_privacy": "public",
        "description_privacy": "private",
    }
    assert _map_privacy(privacy) == PrivacyLevel.PRIVATE


def test_map_privacy_defaults_to_private_for_missing_block():
    # No privacy info at all => assume private (PK historical default).
    assert _map_privacy(None) == PrivacyLevel.PRIVATE
    assert _map_privacy({}) == PrivacyLevel.PRIVATE


def test_parse_iso_accepts_zulu_and_offset():
    expected = datetime(2025, 1, 4, 12, 0, tzinfo=UTC)
    assert _parse_iso("2025-01-04T12:00:00Z") == expected
    assert _parse_iso("2025-01-04T12:00:00+00:00") == expected


def test_parse_iso_returns_none_for_garbage():
    assert _parse_iso(None) is None
    assert _parse_iso("not-a-timestamp") is None
    assert _parse_iso(123) is None


def test_preview_summarises_minimal_export():
    data = {
        "name": "Tiny",
        "members": [{"id": "alpha", "name": "Alpha"}],
        "groups": [],
        "switches": [
            {"timestamp": "2025-01-01T10:00:00Z", "members": ["alpha"]},
            {"timestamp": "2025-01-01T11:00:00Z", "members": []},
        ],
    }
    summary = preview(data)
    assert summary.system_name == "Tiny"
    assert summary.member_count == 1
    assert summary.members[0].id == "alpha"
    assert summary.members[0].name == "Alpha"
    assert summary.switch_count == 2
    assert summary.earliest_switch == datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    assert summary.latest_switch == datetime(2025, 1, 1, 11, 0, tzinfo=UTC)


def test_preview_handles_missing_collections():
    data = {"name": "Empty"}
    summary = preview(data)
    assert summary.system_name == "Empty"
    assert summary.member_count == 0
    assert summary.group_count == 0
    assert summary.switch_count == 0
    assert summary.earliest_switch is None
    assert summary.latest_switch is None


def test_preview_count_override_respects_paged_api_preview():
    """The live-API preview path can pass an explicit override when only
    a single page of switches was sampled."""
    data = {"switches": [{"timestamp": "2025-01-01T10:00:00Z", "members": []}]}
    summary = preview(data, switch_count_override=42)
    assert summary.switch_count == 42

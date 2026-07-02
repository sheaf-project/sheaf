"""Unit tests for the standalone front-history serializers (CSV/JSON/ICS).

These exercise the pure serialization layer directly with synthetic rows -
no DB or running stack needed. The DB load + decrypt path is covered by
the export job e2e tests.
"""

import csv
import io
import json
from datetime import UTC, datetime

from sheaf.services.front_history_export import serialize_front_history

_EXPORTED_AT = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)


def _rows():
    return [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "started_at": datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC),
            "ended_at": datetime(2026, 6, 1, 11, 30, 0, tzinfo=UTC),
            "members": ["Alice", "Bob"],
            "custom_status": "interview, then lunch",  # comma -> must quote
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "started_at": datetime(2026, 6, 2, 14, 0, 0, tzinfo=UTC),
            "ended_at": None,  # ongoing
            "members": ["Carol"],
            "custom_status": None,
        },
    ]


def test_csv_shape_and_quoting():
    raw = serialize_front_history(_rows(), "Test System", "fronts_csv", _EXPORTED_AT)
    text = raw.decode("utf-8")
    assert text.startswith("﻿")  # UTF-8 BOM for spreadsheets
    reader = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    header = reader[0]
    assert header == [
        "started_at",
        "ended_at",
        "duration_seconds",
        "duration",
        "member_count",
        "members",
        "custom_status",
    ]
    closed = reader[1]
    assert closed[2] == "9000"  # 2.5h in seconds
    assert closed[3] == "2:30:00"
    assert closed[4] == "2"
    assert closed[5] == "Alice; Bob"
    assert closed[6] == "interview, then lunch"  # csv module quoted the comma
    ongoing = reader[2]
    assert ongoing[1] == ""  # no ended_at
    assert ongoing[2] == "" and ongoing[3] == ""  # blank duration
    assert ongoing[4] == "1"


def test_csv_neutralises_formula_injection():
    rows = [
        {
            "id": "33333333-3333-3333-3333-333333333333",
            "started_at": datetime(2026, 6, 3, 8, 0, 0, tzinfo=UTC),
            "ended_at": datetime(2026, 6, 3, 9, 0, 0, tzinfo=UTC),
            "members": ["=cmd|' /c calc'!A1"],  # malicious member name
            "custom_status": "+1+2",  # malicious status
        },
    ]
    raw = serialize_front_history(rows, None, "fronts_csv", _EXPORTED_AT)
    reader = list(csv.reader(io.StringIO(raw.decode("utf-8").lstrip("﻿"))))
    row = reader[1]
    # A leading =, +, -, @ is prefixed with ' so a spreadsheet treats the
    # shared file's cell as literal text, not a formula.
    assert row[5].startswith("'=")  # members cell
    assert row[6].startswith("'+")  # custom_status cell


def test_json_shape():
    raw = serialize_front_history(_rows(), "Test System", "fronts_json", _EXPORTED_AT)
    doc = json.loads(raw)
    assert doc["sheaf_front_history_version"] == "1"
    assert doc["system_name"] == "Test System"
    assert doc["front_count"] == 2
    assert doc["exported_at"] == _EXPORTED_AT.isoformat()
    first, second = doc["fronts"]
    assert first["members"] == ["Alice", "Bob"]
    assert first["ended_at"] is not None
    assert second["ended_at"] is None
    assert second["custom_status"] is None


def test_ics_structure_and_escaping():
    raw = serialize_front_history(_rows(), "Test; System", "fronts_ics", _EXPORTED_AT)
    text = raw.decode("utf-8")
    assert "\r\n" in text  # CRLF line endings
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.rstrip().endswith("END:VCALENDAR")
    assert text.count("BEGIN:VEVENT") == 2
    # UTC timestamps in iCalendar basic format.
    assert "DTSTART:20260601T090000Z" in text
    assert "DTEND:20260601T113000Z" in text
    # Comma in the custom_status DESCRIPTION is escaped.
    assert "DESCRIPTION:interview\\, then lunch" in text
    # The ongoing front has no real end: it is marked and spans to export time.
    assert "SUMMARY:Carol (ongoing)" in text
    assert "DTEND:20260624T120000Z" in text  # == exported_at
    # Semicolon in the calendar name is escaped, not a property separator.
    assert "X-WR-CALNAME:Test\\; System front history" in text


def test_ics_neutralises_carriage_return_injection():
    # A member name / custom_status / system name carrying a CR (bare or as
    # CRLF) must not be able to terminate its content line and inject its own
    # calendar properties or a sibling VEVENT into whoever imports the feed.
    rows = [
        {
            "id": "44444444-4444-4444-4444-444444444444",
            "started_at": datetime(2026, 6, 4, 8, 0, 0, tzinfo=UTC),
            "ended_at": datetime(2026, 6, 4, 9, 0, 0, tzinfo=UTC),
            "members": ["Mallory\r\nATTENDEE:mailto:evil@example.com"],
            "custom_status": "line1\rline2",  # bare CR
        },
    ]
    raw = serialize_front_history(
        rows, "Sys\r\nX-WR-CALDESC:pwned", "fronts_ics", _EXPORTED_AT
    )
    text = raw.decode("utf-8")
    # Still exactly one event: the CR did not open a second VEVENT.
    assert text.count("BEGIN:VEVENT") == 1
    # The injected ATTENDEE stays inside SUMMARY as an escaped newline, never
    # a content line of its own.
    assert "\r\nATTENDEE:" not in text
    assert "SUMMARY:Mallory\\nATTENDEE:mailto:evil@example.com" in text
    # A bare CR in the DESCRIPTION is escaped too, not a line break.
    assert "DESCRIPTION:line1\\nline2" in text
    # The calendar name likewise cannot inject a sibling property.
    assert "\r\nX-WR-CALDESC:" not in text
    assert "X-WR-CALNAME:Sys\\nX-WR-CALDESC:pwned front history" in text


def test_unknown_format_raises():
    try:
        serialize_front_history([], None, "fronts_xml", _EXPORTED_AT)
    except ValueError as exc:
        assert "fronts_xml" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for unknown format")

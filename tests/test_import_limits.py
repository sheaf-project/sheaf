"""Unit tests for the importer business-cap helpers.

Pure - no DB / stack. Exercises the clamp primitives and the warning
rendering that the preview surfaces.
"""

from __future__ import annotations

from sheaf.services import import_limits as il


def test_clamp_str_under_cap_is_untouched():
    report = il.ClampReport()
    assert il.clamp_str("short", il.M_NAME, report=report) == "short"
    assert report.empty


def test_clamp_str_none_passes_through():
    report = il.ClampReport()
    assert il.clamp_str(None, il.M_NAME, report=report) is None
    assert report.empty


def test_clamp_str_truncates_and_records():
    report = il.ClampReport()
    out = il.clamp_str("x" * 250, il.M_NAME, report=report)
    assert out == "x" * 100
    assert not report.empty
    assert report.to_warnings() == [
        "1 member name will be shortened to 100 characters"
    ]


def test_clamp_str_silent_without_report():
    out = il.clamp_str("x" * 250, il.M_NAME)
    assert out == "x" * 100  # still clamps - the backstop is unconditional


def test_clamp_list_caps_count_and_records():
    report = il.ClampReport()
    out = il.clamp_list(list(range(50)), il.POLL_OPTIONS_COUNT, report=report)
    assert out == list(range(20))
    assert report.to_warnings() == [
        "1 poll's option list will be trimmed to 20 entries"
    ]


def test_clamp_list_under_cap_untouched():
    report = il.ClampReport()
    out = il.clamp_list([1, 2, 3], il.POLL_OPTIONS_COUNT, report=report)
    assert out == [1, 2, 3]
    assert report.empty


def test_clamp_list_none_passes_through():
    assert il.clamp_list(None, il.POLL_OPTIONS_COUNT) is None


def test_warnings_pluralise_on_count():
    report = il.ClampReport()
    for _ in range(3):
        il.clamp_str("x" * 250, il.M_NAME, report=report)
    assert report.to_warnings() == [
        "3 member names will be shortened to 100 characters"
    ]


def test_warnings_are_deterministically_ordered():
    report = il.ClampReport()
    # Record in a deliberately non-alphabetical order.
    il.clamp_str("x" * 200, il.SYS_NAME, report=report)
    il.clamp_str("x" * 200, il.M_NAME, report=report)
    il.clamp_list(list(range(150)), il.CF_CHOICES_COUNT, report=report)
    warnings = report.to_warnings()
    # str hits sorted by label, then list hits sorted by label.
    assert warnings == [
        "1 member name will be shortened to 100 characters",
        "1 system name will be shortened to 100 characters",
        "1 custom field's choice list will be trimmed to 100 entries",
    ]


def test_distinct_fields_each_get_a_line():
    report = il.ClampReport()
    il.clamp_str("x" * 200, il.M_NAME, report=report)
    il.clamp_str("x" * 200, il.M_DISPLAY_NAME, report=report)
    assert len(report.to_warnings()) == 2


def test_caps_match_schema_limits():
    # Guard against drift from the schema constants these mirror.
    assert il.M_NAME.limit == 100
    assert il.M_NOTE.limit == 5000
    assert il.M_BIRTHDAY.limit == 10
    assert il.M_PLURALKIT_ID.limit == 8
    assert il.M_EMOJI.limit == 8
    assert il.TAG_NAME.limit == 50
    assert il.CF_CHOICES_COUNT.limit == 100
    assert il.CF_CHOICE.limit == 100
    assert il.POLL_OPTIONS_COUNT.limit == 20
    assert il.POLL_OPTION.limit == 200
    assert il.MESSAGE_BODY.limit == 5000
    assert il.JOURNAL_TITLE.limit == 200
    assert il.REMINDER_BODY.limit == 2000

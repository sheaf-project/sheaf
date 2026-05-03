"""Quiet-hours window math, with timezone awareness.

Pure-function tests over `_quiet_hours_end`. The dispatcher uses this to
decide whether an outbox row is deliverable now or should be requeued
past the end of the channel's quiet window. Comparisons happen in the
channel's timezone (zoneinfo, so DST boundaries are honoured) and the
returned timestamp is always UTC for storage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from sheaf.schemas.notifications import QuietHours
from sheaf.services.notifications.dispatcher import _quiet_hours_end

# ---------------------------------------------------------------------------
# UTC behaviour preserved (the v1 default)
# ---------------------------------------------------------------------------


def test_utc_inside_same_day_window():
    """22:00-23:00 UTC; now is 22:30 UTC -> requeue to 23:00 UTC."""
    qh = {"start": "22:00", "end": "23:00", "tz": "UTC"}
    now = datetime(2026, 5, 2, 22, 30, tzinfo=UTC)
    end = _quiet_hours_end(qh, now)
    assert end == datetime(2026, 5, 2, 23, 0, tzinfo=UTC)


def test_utc_outside_window_returns_none():
    qh = {"start": "22:00", "end": "23:00", "tz": "UTC"}
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    assert _quiet_hours_end(qh, now) is None


def test_utc_cross_midnight_late_evening_half():
    """22:00-07:00 UTC, now is 23:30 UTC -> requeue to 07:00 next day UTC."""
    qh = {"start": "22:00", "end": "07:00", "tz": "UTC"}
    now = datetime(2026, 5, 2, 23, 30, tzinfo=UTC)
    end = _quiet_hours_end(qh, now)
    assert end == datetime(2026, 5, 3, 7, 0, tzinfo=UTC)


def test_utc_cross_midnight_early_morning_half():
    """22:00-07:00 UTC, now is 03:00 UTC -> requeue to 07:00 same day."""
    qh = {"start": "22:00", "end": "07:00", "tz": "UTC"}
    now = datetime(2026, 5, 2, 3, 0, tzinfo=UTC)
    end = _quiet_hours_end(qh, now)
    assert end == datetime(2026, 5, 2, 7, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Non-UTC: window is interpreted in the channel's local time
# ---------------------------------------------------------------------------


def test_berlin_inside_window_returns_local_end_in_utc():
    """Berlin in May = CEST (+02:00). Quiet 22:00-07:00 Berlin local.
    Now is 23:00 Berlin = 21:00 UTC. End is 07:00 next day Berlin =
    05:00 UTC."""
    qh = {"start": "22:00", "end": "07:00", "tz": "Europe/Berlin"}
    now = datetime(2026, 5, 2, 21, 0, tzinfo=UTC)  # 23:00 Berlin
    end = _quiet_hours_end(qh, now)
    assert end == datetime(2026, 5, 3, 5, 0, tzinfo=UTC)


def test_la_outside_window_returns_none():
    """LA in May = PDT (-07:00). Quiet 22:00-07:00 LA local. Now is
    14:00 LA = 21:00 UTC. Outside the window."""
    qh = {"start": "22:00", "end": "07:00", "tz": "America/Los_Angeles"}
    now = datetime(2026, 5, 2, 21, 0, tzinfo=UTC)  # 14:00 LA
    assert _quiet_hours_end(qh, now) is None


def test_tokyo_inside_window_returns_local_end_in_utc():
    """Tokyo (JST, no DST). Quiet 23:00-06:00 Tokyo. Now is 02:00 Tokyo
    = 17:00 UTC previous day. End is 06:00 Tokyo same Tokyo-day =
    21:00 UTC previous day."""
    qh = {"start": "23:00", "end": "06:00", "tz": "Asia/Tokyo"}
    now = datetime(2026, 5, 2, 17, 0, tzinfo=UTC)  # 02:00 next day Tokyo
    end = _quiet_hours_end(qh, now)
    # end should be 06:00 Tokyo on May 3 = 21:00 UTC on May 2.
    assert end == datetime(2026, 5, 2, 21, 0, tzinfo=UTC)


def test_dst_spring_forward_window_still_anchored_to_local_clock():
    """US DST springs forward 2026-03-08 02:00 -> 03:00 EDT.
    Quiet 22:00-07:00 New York. Caller is at 06:30 EDT = 10:30 UTC on
    2026-03-08. Window should requeue to 07:00 EDT = 11:00 UTC, i.e.
    the local clock is what matters, not the wall-clock-elapsed time."""
    qh = {"start": "22:00", "end": "07:00", "tz": "America/New_York"}
    now = datetime(2026, 3, 8, 10, 30, tzinfo=UTC)  # 06:30 EDT
    end = _quiet_hours_end(qh, now)
    assert end == datetime(2026, 3, 8, 11, 0, tzinfo=UTC)  # 07:00 EDT


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_unknown_tz_falls_back_to_utc():
    """Bad tz somehow stored should not crash dispatch — fall back to UTC
    so we still respect SOME window boundary."""
    qh = {"start": "22:00", "end": "07:00", "tz": "Mars/Olympus_Mons"}
    now = datetime(2026, 5, 2, 22, 30, tzinfo=UTC)
    end = _quiet_hours_end(qh, now)
    # Treated as UTC: 22:30 UTC inside 22-07 window -> 07:00 next day UTC.
    assert end == datetime(2026, 5, 3, 7, 0, tzinfo=UTC)


def test_empty_quiet_hours_returns_none():
    assert _quiet_hours_end(None, datetime.now(UTC)) is None
    assert _quiet_hours_end({}, datetime.now(UTC)) is None


def test_malformed_time_returns_none():
    qh = {"start": "not-a-time", "end": "07:00", "tz": "UTC"}
    assert _quiet_hours_end(qh, datetime.now(UTC)) is None


# ---------------------------------------------------------------------------
# Schema validator
# ---------------------------------------------------------------------------


def test_quiet_hours_schema_accepts_iana_zone():
    qh = QuietHours(start="22:00", end="07:00", tz="Europe/Berlin")
    assert qh.tz == "Europe/Berlin"


def test_quiet_hours_schema_accepts_utc_default():
    qh = QuietHours(start="22:00", end="07:00")
    assert qh.tz == "UTC"


def test_quiet_hours_schema_rejects_unknown_zone():
    with pytest.raises(ValueError, match="unknown IANA timezone"):
        QuietHours(start="22:00", end="07:00", tz="Mars/Olympus_Mons")


def test_quiet_hours_schema_rejects_offset_string():
    """Reject `+02:00`-style offsets — IANA names only, since DST handling
    needs the rule set, not just a current offset."""
    with pytest.raises(ValueError, match="unknown IANA timezone"):
        QuietHours(start="22:00", end="07:00", tz="+02:00")


def test_quiet_hours_returned_value_is_always_utc():
    """Every non-None return must be a UTC datetime — outbox rows store
    deliver_after as UTC."""
    qh = {"start": "22:00", "end": "07:00", "tz": "Asia/Tokyo"}
    end = _quiet_hours_end(
        qh, datetime(2026, 5, 2, 17, 0, tzinfo=UTC)
    )
    assert end is not None
    assert end.utcoffset() == ZoneInfo("UTC").utcoffset(end)

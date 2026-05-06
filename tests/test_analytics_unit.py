"""Unit tests for the analytics aggregation primitives."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sheaf.services.analytics import (
    FrontInterval,
    _bucket_into_hours,
    aggregate,
    clip_intervals,
)


def _utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


# --- _bucket_into_hours -----------------------------------------------------


def test_bucket_into_hours_simple_within_one_hour():
    """A 30-minute slice entirely inside 14:xx UTC lands in bucket 14."""
    start = _utc(2026, 1, 1, 14, 15)
    end = _utc(2026, 1, 1, 14, 45)
    buckets = _bucket_into_hours(start, end, ZoneInfo("UTC"))
    assert buckets[14] == 30 * 60
    assert sum(buckets[:14] + buckets[15:]) == 0


def test_bucket_into_hours_spans_multiple_hours():
    """14:30 to 16:15 splits as 30/60/15 minutes across buckets 14, 15, 16."""
    start = _utc(2026, 1, 1, 14, 30)
    end = _utc(2026, 1, 1, 16, 15)
    buckets = _bucket_into_hours(start, end, ZoneInfo("UTC"))
    assert buckets[14] == 30 * 60
    assert buckets[15] == 60 * 60
    assert buckets[16] == 15 * 60
    assert sum(buckets) == 105 * 60


def test_bucket_into_hours_respects_target_timezone():
    """A 23:30-00:30 UTC session in NYC (UTC-5 in winter) is 18:30-19:30."""
    start = _utc(2026, 1, 1, 23, 30)
    end = _utc(2026, 1, 2, 0, 30)
    buckets = _bucket_into_hours(start, end, ZoneInfo("America/New_York"))
    assert buckets[18] == 30 * 60
    assert buckets[19] == 30 * 60
    assert buckets[23] == 0
    assert buckets[0] == 0


def test_bucket_into_hours_zero_duration():
    start = _utc(2026, 1, 1, 14, 0)
    buckets = _bucket_into_hours(start, start, ZoneInfo("UTC"))
    assert buckets == [0] * 24


# --- clip_intervals --------------------------------------------------------


def test_clip_intervals_keeps_fully_inside():
    since = _utc(2026, 1, 1)
    until = _utc(2026, 2, 1)
    rows = [(_utc(2026, 1, 5, 10), _utc(2026, 1, 5, 14), [uuid.uuid4()])]
    intervals = clip_intervals(rows, since=since, until=until)
    assert len(intervals) == 1
    assert intervals[0].start == _utc(2026, 1, 5, 10)
    assert intervals[0].end == _utc(2026, 1, 5, 14)


def test_clip_intervals_drops_fully_outside():
    since = _utc(2026, 2, 1)
    until = _utc(2026, 3, 1)
    rows = [
        (_utc(2026, 1, 5, 10), _utc(2026, 1, 5, 14), [uuid.uuid4()]),
        (_utc(2026, 4, 5, 10), _utc(2026, 4, 5, 14), [uuid.uuid4()]),
    ]
    assert clip_intervals(rows, since=since, until=until) == []


def test_clip_intervals_clips_window_overlap():
    since = _utc(2026, 1, 10)
    until = _utc(2026, 1, 20)
    member = uuid.uuid4()
    rows = [
        (_utc(2026, 1, 5), _utc(2026, 1, 15), [member]),
        (_utc(2026, 1, 18), _utc(2026, 1, 25), [member]),
    ]
    intervals = clip_intervals(rows, since=since, until=until)
    assert len(intervals) == 2
    assert intervals[0].start == since
    assert intervals[0].end == _utc(2026, 1, 15)
    assert intervals[1].start == _utc(2026, 1, 18)
    assert intervals[1].end == until


def test_clip_intervals_treats_ongoing_as_until():
    since = _utc(2026, 1, 1)
    until = _utc(2026, 2, 1)
    member = uuid.uuid4()
    rows = [(_utc(2026, 1, 20, 12), None, [member])]  # ongoing
    intervals = clip_intervals(rows, since=since, until=until)
    assert len(intervals) == 1
    assert intervals[0].end == until


# --- aggregate -------------------------------------------------------------


def test_aggregate_single_member_simple():
    member = uuid.uuid4()
    since = _utc(2026, 1, 1)
    until = _utc(2026, 1, 2)
    intervals = [
        FrontInterval(
            start=_utc(2026, 1, 1, 10),
            end=_utc(2026, 1, 1, 12),
            member_ids=[member],
        )
    ]
    result = aggregate(
        intervals,
        since=since,
        until=until,
        tz=ZoneInfo("UTC"),
        member_is_custom_front={member: False},
    )
    assert result[member]["total_seconds"] == 7200
    assert result[member]["session_count"] == 1
    assert result[member]["longest_session_seconds"] == 7200
    assert result[member]["is_custom_front"] is False
    # 1 day window = 86400s, 7200s = 8.33%
    assert abs(result[member]["percent_of_window"] - 8.33) < 0.01


def test_aggregate_co_fronting_double_counts():
    """Co-fronting is the SimplyPlural convention: both Alice and Bob get
    the full duration credited individually."""
    alice = uuid.uuid4()
    bob = uuid.uuid4()
    since = _utc(2026, 1, 1)
    until = _utc(2026, 1, 2)
    intervals = [
        FrontInterval(
            start=_utc(2026, 1, 1, 10),
            end=_utc(2026, 1, 1, 11),
            member_ids=[alice, bob],
        )
    ]
    result = aggregate(
        intervals,
        since=since,
        until=until,
        tz=ZoneInfo("UTC"),
        member_is_custom_front={alice: False, bob: False},
    )
    assert result[alice]["total_seconds"] == 3600
    assert result[bob]["total_seconds"] == 3600


def test_aggregate_longest_session_tracked_correctly():
    member = uuid.uuid4()
    since = _utc(2026, 1, 1)
    until = _utc(2026, 1, 5)
    intervals = [
        FrontInterval(
            start=_utc(2026, 1, 1, 10),
            end=_utc(2026, 1, 1, 11),  # 1 hour
            member_ids=[member],
        ),
        FrontInterval(
            start=_utc(2026, 1, 2, 10),
            end=_utc(2026, 1, 2, 14),  # 4 hours, longest
            member_ids=[member],
        ),
        FrontInterval(
            start=_utc(2026, 1, 3, 10),
            end=_utc(2026, 1, 3, 12),  # 2 hours
            member_ids=[member],
        ),
    ]
    result = aggregate(
        intervals,
        since=since,
        until=until,
        tz=ZoneInfo("UTC"),
        member_is_custom_front={member: False},
    )
    assert result[member]["session_count"] == 3
    assert result[member]["longest_session_seconds"] == 4 * 3600
    assert result[member]["total_seconds"] == 7 * 3600


def test_aggregate_marks_custom_fronts():
    custom = uuid.uuid4()
    intervals = [
        FrontInterval(
            start=_utc(2026, 1, 1, 0),
            end=_utc(2026, 1, 1, 8),
            member_ids=[custom],
        )
    ]
    result = aggregate(
        intervals,
        since=_utc(2026, 1, 1),
        until=_utc(2026, 1, 2),
        tz=ZoneInfo("UTC"),
        member_is_custom_front={custom: True},
    )
    assert result[custom]["is_custom_front"] is True


def test_aggregate_zero_window_does_not_divide_by_zero():
    """A degenerate window with since == until should yield zeros."""
    member = uuid.uuid4()
    ts = _utc(2026, 1, 1)
    result = aggregate(
        [],
        since=ts,
        until=ts,
        tz=ZoneInfo("UTC"),
        member_is_custom_front={member: False},
    )
    assert result == {}


def test_aggregate_hour_distribution_in_target_tz():
    """Verify the per-member hour-of-day buckets respect the supplied tz."""
    member = uuid.uuid4()
    intervals = [
        FrontInterval(
            start=_utc(2026, 1, 1, 23, 0),  # 18:00 NYC (winter, UTC-5)
            end=_utc(2026, 1, 2, 1, 0),  # 20:00 NYC
            member_ids=[member],
        )
    ]
    result = aggregate(
        intervals,
        since=_utc(2026, 1, 1),
        until=_utc(2026, 1, 3),
        tz=ZoneInfo("America/New_York"),
        member_is_custom_front={member: False},
    )
    buckets = result[member]["hour_of_day_seconds"]
    assert buckets[18] == 3600
    assert buckets[19] == 3600
    assert buckets[23] == 0


def test_clip_then_aggregate_end_to_end():
    """Full pipeline: a raw front overlapping the window edge gets clipped,
    then aggregated. Only the inside-window time should count."""
    member = uuid.uuid4()
    since = _utc(2026, 1, 1)
    until = _utc(2026, 1, 2)
    rows = [
        # Started 1 hour before window, ended 1 hour into window: only 1h counts
        (_utc(2025, 12, 31, 23), _utc(2026, 1, 1, 1), [member]),
        # Fully inside: 2h
        (_utc(2026, 1, 1, 12), _utc(2026, 1, 1, 14), [member]),
        # Ongoing, started 30 min before window end: 30min counts
        (_utc(2026, 1, 1, 23, 30), None, [member]),
    ]
    intervals = clip_intervals(rows, since=since, until=until)
    result = aggregate(
        intervals,
        since=since,
        until=until,
        tz=ZoneInfo("UTC"),
        member_is_custom_front={member: False},
    )
    expected_seconds = 3600 + 7200 + 1800
    assert result[member]["total_seconds"] == expected_seconds
    assert result[member]["session_count"] == 3


def test_aggregate_handles_dst_spring_forward():
    """DST 'spring forward' eats an hour: 02:00 jumps to 03:00 in NYC.
    A session straddling that should not double-count or skip seconds.

    On 2026-03-08, NYC clocks jump from 02:00 to 03:00. A session from
    01:30 to 03:30 local is 1.5 hours of wall-clock time (because 02:xx
    doesn't exist that day). We expect 30 min in bucket 1, 30 min in
    bucket 3 — bucket 2 is empty.
    """
    member = uuid.uuid4()
    nyc = ZoneInfo("America/New_York")
    # 01:30 NYC local on the spring-forward day, in UTC is 06:30
    start = datetime(2026, 3, 8, 1, 30, tzinfo=nyc).astimezone(UTC)
    # 03:30 NYC local same day, UTC is 07:30
    end = datetime(2026, 3, 8, 3, 30, tzinfo=nyc).astimezone(UTC)

    intervals = [FrontInterval(start=start, end=end, member_ids=[member])]
    result = aggregate(
        intervals,
        since=start - timedelta(hours=1),
        until=end + timedelta(hours=1),
        tz=nyc,
        member_is_custom_front={member: False},
    )
    buckets = result[member]["hour_of_day_seconds"]
    # 01:30 -> 02:00 (in nominal local time) is 30 minutes → bucket 1
    # 03:00 -> 03:30 is 30 minutes → bucket 3
    # bucket 2 should be empty since that hour was skipped
    assert buckets[1] == 30 * 60
    assert buckets[2] == 0
    assert buckets[3] == 30 * 60
    # Total wall-clock time was 1 hour
    assert sum(buckets) == 60 * 60
    assert result[member]["total_seconds"] == 60 * 60

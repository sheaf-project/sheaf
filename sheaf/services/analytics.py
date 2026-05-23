"""Fronting analytics aggregation.

This module turns the system's Front table into per-member time-on-front
summaries over a window. The work is intentionally pure Python and
operates on a list of `(started_at, ended_at, [member_ids])` tuples, so
it's easy to test in isolation and easy for the API endpoint to feed in
whatever query results it pulled.

Key semantics:

- Co-fronting double-counts. If Alice and Bob co-front for an hour, both
  Alice and Bob accrue +3600 seconds. This matches SimplyPlural's
  analytics shape and is the reading users expect for "how much did
  Alice front this month".

- Ongoing fronts (ended_at IS NULL) are treated as ending at `until` for
  the purposes of this window. The session is closed virtually for the
  calculation, but is not modified in storage.

- Fronts overlapping the window edge are clipped — only the portion
  inside [since, until] counts.

- Hour-of-day bucketing happens in the supplied timezone (zoneinfo). A
  session that crosses an hour boundary in the target timezone is split
  proportionally: 30 minutes in 14:xx, 30 minutes in 15:xx, etc.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass
class FrontInterval:
    """One front's contribution to the analytics window.

    Already clipped to the window — `start` and `end` are guaranteed to
    fall inside [since, until]. `member_ids` is the (possibly empty)
    list of members who were fronting during this interval.
    """

    start: datetime
    end: datetime
    member_ids: list[uuid.UUID]


@dataclass
class _MemberAccumulator:
    total_seconds: int = 0
    session_count: int = 0
    longest_session_seconds: int = 0
    hour_of_day_seconds: list[int] = field(default_factory=lambda: [0] * 24)


def aggregate(
    intervals: Iterable[FrontInterval],
    *,
    since: datetime,
    until: datetime,
    tz: ZoneInfo,
    member_is_custom_front: dict[uuid.UUID, bool],
) -> dict[uuid.UUID, dict]:
    """Walk over front intervals and produce per-member stats.

    Returns a mapping from member_id to a dict that the caller can use
    to construct `MemberFrontingStats`. Members with zero front time
    (within the window) are still present so the UI can show "no
    fronting time recorded" rather than mysteriously dropping them.

    Custom-front membership is supplied externally because the analytics
    layer doesn't load Member rows itself — the endpoint already has
    them and passes the lookup through.
    """
    window_seconds = max(0, int((until - since).total_seconds()))
    accumulators: dict[uuid.UUID, _MemberAccumulator] = {}

    for interval in intervals:
        duration = (interval.end - interval.start).total_seconds()
        if duration <= 0:
            continue

        # Hour-of-day distribution shared across all members on this front.
        hour_buckets = _bucket_into_hours(interval.start, interval.end, tz)

        for member_id in interval.member_ids:
            acc = accumulators.setdefault(member_id, _MemberAccumulator())
            acc.total_seconds += int(duration)
            acc.session_count += 1
            if duration > acc.longest_session_seconds:
                acc.longest_session_seconds = int(duration)
            for idx, secs in enumerate(hour_buckets):
                acc.hour_of_day_seconds[idx] += secs

    # Materialise into the dict shape MemberFrontingStats expects.
    out: dict[uuid.UUID, dict] = {}
    for member_id, acc in accumulators.items():
        out[member_id] = {
            "member_id": member_id,
            "is_custom_front": member_is_custom_front.get(member_id, False),
            "total_seconds": acc.total_seconds,
            "percent_of_window": (
                round(100.0 * acc.total_seconds / window_seconds, 2)
                if window_seconds > 0
                else 0.0
            ),
            "session_count": acc.session_count,
            "longest_session_seconds": acc.longest_session_seconds,
            "hour_of_day_seconds": acc.hour_of_day_seconds,
        }
    return out


def _bucket_into_hours(
    start: datetime, end: datetime, tz: ZoneInfo
) -> list[int]:
    """Split [start, end] into 24 hour-of-day buckets in the supplied tz.

    The duration is expressed in seconds. Sessions that cross hour
    boundaries are divided proportionally — a session from 14:30 local
    to 16:15 local accrues 30 minutes to bucket 14, 60 minutes to bucket
    15, and 15 minutes to bucket 16.

    The walk is always anchored in UTC: cursor is a UTC-aware datetime,
    we look up the local hour for `.hour` purposes, and we compute the
    next local-hour boundary in local time then convert back to UTC for
    the slice end. This is the only safe way to handle DST transitions:

    - Spring forward: a wall-time hour is skipped. The local hour after
      cursor's hour might land at the same UTC instant; the loop bails
      out via the `<= cursor` guard and advances by a minute, which is
      enough because the gap is exactly 1 hour wide.
    - Fall back: a wall-time hour repeats. zoneinfo's default fold=0
      picks the first occurrence, so the natural UTC progression
      handles it correctly.

    Datetime subtraction between two same-tz aware datetimes ignores
    DST entirely (Python treats them as naive), so duration math must
    happen on UTC-tagged values. That's why everything stays in UTC
    here except for the local hour lookup.

    Worst case for a year-long ongoing front is ~8760 iterations,
    which is fine — analytics is a foreground request and one such row
    in real usage is rare.
    """
    buckets = [0] * 24
    if end <= start:
        return buckets

    # Normalise to UTC up front so duration math is always on UTC values.
    cursor = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)

    while cursor < end_utc:
        local = cursor.astimezone(tz)
        # The next local-hour boundary, expressed in local time, then
        # converted back to UTC. zoneinfo handles DST in astimezone().
        next_local = local.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=1
        )
        next_utc = next_local.astimezone(UTC)
        # If the wall-time addition collapsed across a spring-forward gap
        # (next_utc lands at or before cursor), nudge forward by a minute
        # so we make progress. The gap is exactly one wall hour, so a
        # minute is plenty.
        if next_utc <= cursor:
            next_utc = cursor + timedelta(minutes=1)
        slice_end = min(next_utc, end_utc)
        slice_secs = int((slice_end - cursor).total_seconds())
        if slice_secs > 0:
            buckets[local.hour] += slice_secs
        cursor = slice_end

    return buckets


def score_recent_fronters(
    intervals: Iterable[FrontInterval],
    *,
    now: datetime,
    half_life_days: float = 30.0,
) -> dict[uuid.UUID, float]:
    """Recency-weighted fronting score per member, for quick-switch ranking.

    Each (window-clipped) front contributes
    ``duration_seconds * 0.5 ** (age_days / half_life_days)`` to every
    member who was fronting, where age is measured from the front's end
    (which is `now` for an ongoing front) back to `now`. So long and
    recent fronts score highest, and old activity decays smoothly rather
    than dropping off a window edge. Co-fronting counts for everyone, the
    same as `aggregate`.

    Returns member_id -> score. Members with no fronting time in the
    supplied intervals are simply absent (caller treats them as 0).
    """
    decay_per_day = math.log(2) / half_life_days
    scores: dict[uuid.UUID, float] = {}
    for interval in intervals:
        duration = (interval.end - interval.start).total_seconds()
        if duration <= 0:
            continue
        age_days = max(0.0, (now - interval.end).total_seconds() / 86400.0)
        weight = duration * math.exp(-decay_per_day * age_days)
        for member_id in interval.member_ids:
            scores[member_id] = scores.get(member_id, 0.0) + weight
    return scores


def clip_intervals(
    rows: Iterable[tuple[datetime, datetime | None, list[uuid.UUID]]],
    *,
    since: datetime,
    until: datetime,
) -> list[FrontInterval]:
    """Clip raw `(started_at, ended_at, member_ids)` tuples to the window.

    Ongoing fronts (ended_at IS NULL) are treated as ending at `until`.
    Fronts that don't overlap the window at all are dropped.
    """
    out: list[FrontInterval] = []
    for started_at, ended_at, member_ids in rows:
        effective_end = ended_at if ended_at is not None else until
        if effective_end <= since or started_at >= until:
            continue
        clipped_start = max(started_at, since)
        clipped_end = min(effective_end, until)
        if clipped_end <= clipped_start:
            continue
        out.append(
            FrontInterval(
                start=clipped_start,
                end=clipped_end,
                member_ids=list(member_ids),
            )
        )
    return out

"""Fronting analytics endpoint.

Returns per-member time-on-front summaries over a configurable window.
The aggregation runs in Postgres: per-member totals are a SUM of clipped
front durations, and the hour-of-day distribution is bucketed with a
generate_series walk over local-hour boundaries. Keeping the work in SQL
bounds both memory and event-loop time regardless of how many front rows
the window spans - the response is per-member, not per-front, so only a
handful of rows come back no matter how large the history is.

The pure-Python reference implementation (clip_intervals / aggregate /
_bucket_into_hours) still lives in sheaf.services.analytics; it backs the
quick-switch scorer and the unit tests, and documents the exact semantics
this SQL reproduces (co-fronting double-counts, ongoing fronts close at
`until`, hour-of-day bucketing happens in the supplied timezone).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.analytics import FrontingAnalytics, MemberFrontingStats

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Hard cap on the window length to keep aggregation bounded.
# 5 years is well past anyone's actual usage and prevents accidentally
# walking an unbounded history. Anyone wanting decade-scale stats can
# paginate by year and combine client-side.
_MAX_WINDOW = timedelta(days=365 * 5)


# Per-member totals: SUM/COUNT/MAX of clipped front durations. Ongoing
# fronts (ended_at IS NULL) close at :until, matching the window's upper
# edge (which defaults to now()). Each front's [started_at, ended_at] is
# clipped to [:since, :until] server-side; durations are whole seconds
# (floor), matching the per-front int() the reference aggregator applies.
# Co-fronting double-counts because the front_members join fans a co-front
# out to one row per member. Zero-length clips are dropped by the final
# predicate, matching the reference's `duration <= 0` skip.
_FRONTING_TOTALS_SQL = text(
    """
    SELECT fm.member_id AS member_id,
           SUM(floor(extract(epoch FROM (
               LEAST(COALESCE(f.ended_at, :until), :until)
               - GREATEST(f.started_at, :since)
           ))))::bigint AS total_seconds,
           COUNT(*) AS session_count,
           MAX(floor(extract(epoch FROM (
               LEAST(COALESCE(f.ended_at, :until), :until)
               - GREATEST(f.started_at, :since)
           ))))::bigint AS longest_session_seconds
    FROM fronts f
    JOIN front_members fm ON fm.front_id = f.id
    WHERE f.system_id = :system_id
      AND f.started_at < :until
      AND (f.ended_at IS NULL OR f.ended_at > :since)
      AND LEAST(COALESCE(f.ended_at, :until), :until)
          > GREATEST(f.started_at, :since)
    GROUP BY fm.member_id
    """
)


# Hour-of-day distribution in the requested timezone. Each clipped front is
# split at local-hour boundaries and a slice's seconds are credited to the
# local hour at its start. The generate_series walk is anchored at the UTC
# instant of the front's local-hour floor and steps one UTC hour, so DST
# transitions fall out on their own: a spring-forward hour maps to no UTC
# instant and is skipped, a fall-back hour maps to two and is counted twice.
# This mirrors _bucket_into_hours in sheaf.services.analytics. Per-slice
# floor(...) matches the reference's per-slice int().
_FRONTING_HOURS_SQL = text(
    """
    WITH clipped AS (
        SELECT fm.member_id AS member_id,
               GREATEST(f.started_at, :since) AS cs,
               LEAST(COALESCE(f.ended_at, :until), :until) AS ce
        FROM fronts f
        JOIN front_members fm ON fm.front_id = f.id
        WHERE f.system_id = :system_id
          AND f.started_at < :until
          AND (f.ended_at IS NULL OR f.ended_at > :since)
          AND LEAST(COALESCE(f.ended_at, :until), :until)
              > GREATEST(f.started_at, :since)
    )
    SELECT c.member_id AS member_id,
           extract(hour FROM (gs AT TIME ZONE :tz))::int AS hod,
           SUM(floor(extract(epoch FROM (
               LEAST(gs + interval '1 hour', c.ce)
               - GREATEST(gs, c.cs)
           ))))::bigint AS secs
    FROM clipped c
    CROSS JOIN LATERAL generate_series(
        date_trunc('hour', c.cs AT TIME ZONE :tz) AT TIME ZONE :tz,
        c.ce,
        interval '1 hour'
    ) AS gs
    WHERE LEAST(gs + interval '1 hour', c.ce) > GREATEST(gs, c.cs)
    GROUP BY c.member_id, hod
    """
)


def _parse_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown timezone: {tz_name}",
        ) from exc


@router.get(
    "/fronting",
    response_model=FrontingAnalytics,
    dependencies=[rate_limit(20, 60, "user")],
)
async def fronting_analytics(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    tz: str = Query(default="UTC"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-member fronting summary over the requested window.

    Defaults: `until` = now, `since` = until - 30 days, `tz` = UTC.
    The window is clamped to 5 years; past that, paginate by year and
    combine client-side.

    Co-fronting double-counts: if Alice and Bob co-front for an hour, both
    see +3600 seconds. This matches the reading users expect for "how much
    did Alice front this month".
    """
    now = datetime.now(UTC)
    until_ts = until or now
    since_ts = since or (until_ts - timedelta(days=30))

    if until_ts <= since_ts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`until` must be after `since`.",
        )
    if (until_ts - since_ts) > _MAX_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Window too large; max 5 years.",
        )

    # Validate the timezone up front so a bad tz is a 400, not a SQL error.
    # Postgres does the actual local-hour conversion from the same name.
    _parse_tz(tz)

    window_seconds = int((until_ts - since_ts).total_seconds())

    system_result = await db.execute(
        select(System).where(System.user_id == user.id)
    )
    system = system_result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )

    base_params = {
        "system_id": system.id,
        "since": since_ts,
        "until": until_ts,
    }

    # Per-member totals and hour-of-day distribution, both aggregated in
    # Postgres. Each returns at most one row per member (24 per member for
    # the hour buckets), so the payload back to Python is tiny regardless
    # of how many fronts the window covers.
    totals_result = await db.execute(_FRONTING_TOTALS_SQL, base_params)
    totals = {row.member_id: row for row in totals_result}

    hours_result = await db.execute(
        _FRONTING_HOURS_SQL, {**base_params, "tz": tz}
    )
    hour_buckets: dict[uuid.UUID, list[int]] = {}
    for row in hours_result:
        buckets = hour_buckets.setdefault(row.member_id, [0] * 24)
        buckets[row.hod] = int(row.secs or 0)

    # Member metadata for the response (is_custom_front flag) plus a
    # zero-stats entry for every member so the UI can list members who
    # didn't front in the window without special-casing them. Members are
    # per-system and bounded, so loading them is cheap - it's the fronts
    # table that could be huge, and that never leaves Postgres now.
    members_result = await db.execute(
        select(Member).where(Member.system_id == system.id)
    )
    members = list(members_result.scalars().all())

    member_stats: list[MemberFrontingStats] = []
    for member in members:
        row = totals.get(member.id)
        if row is None:
            # No fronting time in the window; still list them so the UI
            # can sort by total_seconds desc and they fall to the bottom.
            member_stats.append(
                MemberFrontingStats(
                    member_id=member.id,
                    is_custom_front=member.is_custom_front,
                )
            )
            continue
        total_seconds = int(row.total_seconds or 0)
        member_stats.append(
            MemberFrontingStats(
                member_id=member.id,
                is_custom_front=member.is_custom_front,
                total_seconds=total_seconds,
                percent_of_window=(
                    round(100.0 * total_seconds / window_seconds, 2)
                    if window_seconds > 0
                    else 0.0
                ),
                session_count=int(row.session_count or 0),
                longest_session_seconds=int(row.longest_session_seconds or 0),
                hour_of_day_seconds=hour_buckets.get(member.id, [0] * 24),
            )
        )

    member_stats.sort(key=lambda s: s.total_seconds, reverse=True)

    return FrontingAnalytics(
        since=since_ts,
        until=until_ts,
        tz=tz,
        window_seconds=window_seconds,
        members=member_stats,
    )

"""Fronting analytics endpoint.

Returns per-member time-on-front summaries over a configurable window.
See sheaf.services.analytics for the aggregation semantics (co-fronting
double-counts, ongoing fronts close at `until`, hour-of-day bucketing
happens in the supplied timezone).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.models.front import Front
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.analytics import FrontingAnalytics, MemberFrontingStats
from sheaf.services.analytics import (
    aggregate,
    clip_intervals,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Hard cap on the window length to keep aggregation bounded.
# 5 years is well past anyone's actual usage and prevents accidentally
# walking an unbounded history. Anyone wanting decade-scale stats can
# paginate by year and combine client-side.
_MAX_WINDOW = timedelta(days=365 * 5)


def _parse_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown timezone: {tz_name}",
        ) from exc


@router.get("/fronting", response_model=FrontingAnalytics)
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

    Co-fronting double-counts: if Alice and Bob co-front for an hour,
    both see +3600 seconds. This matches the reading users expect for
    "how much did Alice front this month".
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

    zone = _parse_tz(tz)

    system_result = await db.execute(
        select(System).where(System.user_id == user.id)
    )
    system = system_result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )

    # Load fronts overlapping the window. We need ended_at IS NULL OR
    # ended_at > since to keep ongoing fronts and recently-ended ones.
    fronts_result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(
            Front.system_id == system.id,
            Front.started_at < until_ts,
            (Front.ended_at.is_(None)) | (Front.ended_at > since_ts),
        )
    )
    fronts = list(fronts_result.scalars().all())

    rows: list[tuple[datetime, datetime | None, list[uuid.UUID]]] = [
        (f.started_at, f.ended_at, [m.id for m in f.members])
        for f in fronts
    ]
    intervals = clip_intervals(rows, since=since_ts, until=until_ts)

    # Member metadata for the response (is_custom_front flag) plus a
    # zero-stats entry for every member so the UI can list members who
    # didn't front in the window without special-casing them.
    members_result = await db.execute(
        select(Member).where(Member.system_id == system.id)
    )
    members = list(members_result.scalars().all())
    member_is_custom = {m.id: m.is_custom_front for m in members}

    aggregated = aggregate(
        intervals,
        since=since_ts,
        until=until_ts,
        tz=zone,
        member_is_custom_front=member_is_custom,
    )

    # Backfill members with no fronting time so the response includes
    # everyone — UI can sort by total_seconds desc and the no-front
    # members fall to the bottom naturally.
    member_stats: list[MemberFrontingStats] = []
    for member in members:
        if member.id in aggregated:
            member_stats.append(MemberFrontingStats(**aggregated[member.id]))
        else:
            member_stats.append(
                MemberFrontingStats(
                    member_id=member.id,
                    is_custom_front=member.is_custom_front,
                )
            )

    member_stats.sort(key=lambda s: s.total_seconds, reverse=True)

    window_seconds = int((until_ts - since_ts).total_seconds())
    return FrontingAnalytics(
        since=since_ts,
        until=until_ts,
        tz=tz,
        window_seconds=window_seconds,
        members=member_stats,
    )

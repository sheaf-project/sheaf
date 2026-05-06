"""Pydantic models for fronting analytics."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MemberFrontingStats(BaseModel):
    """Per-member fronting summary over the requested window.

    All `*_seconds` fields are integer seconds. Co-fronting counts toward
    each member individually — if Alice and Bob co-front for an hour, both
    Alice and Bob get +3600 to their `total_seconds`.

    `hour_of_day_seconds` is a 24-element array indexed 0..23, expressed
    in the timezone supplied to the endpoint. Front intervals that cross
    hour boundaries are split proportionally.
    """

    member_id: uuid.UUID
    is_custom_front: bool
    total_seconds: int = 0
    percent_of_window: float = 0.0
    session_count: int = 0
    longest_session_seconds: int = 0
    hour_of_day_seconds: list[int] = Field(
        default_factory=lambda: [0] * 24,
        min_length=24,
        max_length=24,
    )


class FrontingAnalytics(BaseModel):
    """Aggregated fronting statistics for the user's system."""

    since: datetime
    until: datetime
    tz: str
    window_seconds: int
    members: list[MemberFrontingStats]

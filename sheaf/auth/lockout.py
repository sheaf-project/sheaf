"""Shared failed-attempt lockout for credentialed endpoints.

`failed_login_count` / `locked_until` on the User row form a single
lockout state consulted and incremented by every endpoint that verifies
a short, brute-forceable credential (password at login, the 6-digit
TOTP code, recovery codes). Keeping these helpers in one module means
failures on one endpoint count toward locking the others, so an
attacker can't sidestep the lockout by hopping between endpoints.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import and_, case, update
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.models.user import User


def ensure_not_locked(user: User) -> None:
    """Raise 423 if the account is inside a failed-attempt lockout window."""
    now = datetime.now(UTC)
    if user.locked_until is not None and user.locked_until > now:
        mins = max(1, math.ceil((user.locked_until - now).total_seconds() / 60))
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                f"Account temporarily locked after repeated failed attempts. "
                f"Try again in {mins} minute{'s' if mins != 1 else ''}."
            ),
        )


async def record_login_failure(db: AsyncSession, user: User) -> None:
    """Increment the user's failed-attempt counter and lock if threshold crossed.

    A single atomic UPDATE handles both the increment and the lockout decision
    so concurrent failed attempts can't race past the threshold. If the user
    has a stale (expired) lockout, the counter resets to 1 for this attempt
    instead of incrementing, so a returning user with one typo doesn't get
    immediately re-locked on top of old failures.
    """
    now = datetime.now(UTC)
    lockout_end = now + timedelta(minutes=settings.login_lockout_minutes)
    threshold = settings.login_max_failures

    new_count = case(
        (
            and_(User.locked_until.is_not(None), User.locked_until < now),
            1,
        ),
        else_=User.failed_login_count + 1,
    )
    new_lock = case(
        (new_count >= threshold, lockout_end),
        else_=None,
    )

    await db.execute(
        update(User)
        .where(User.id == user.id)
        .values(failed_login_count=new_count, locked_until=new_lock)
    )
    await db.commit()
    db.expire(user, ["failed_login_count", "locked_until"])

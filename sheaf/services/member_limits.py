"""Member-count limits (per tier, with per-user override).

Single source of truth for "how many members may this account have" and the
"would this import blow the cap" check. The normal create path
(`POST /v1/members`) and every importer share these so the limit can't be
enforced in one place and silently bypassed in another.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.user import User, UserTier
from sheaf.services.import_parsing import ImportPayloadError

_MEMBER_LIMIT_MAP = {
    UserTier.FREE: lambda: settings.member_limit_free,
    UserTier.PLUS: lambda: settings.member_limit_plus,
    UserTier.SELF_HOSTED: lambda: settings.member_limit_selfhosted,
}


def get_member_limit(user: User) -> int:
    """Effective member limit for a user. 0 means unlimited.

    A per-user override (`user.member_limit`) wins over the tier default,
    so support can lift an individual account without changing its tier.
    """
    if user.member_limit is not None:
        return user.member_limit
    return _MEMBER_LIMIT_MAP.get(user.tier, lambda: 0)()


async def count_members(db: AsyncSession, system_id: uuid.UUID) -> int:
    """Current member count for a system."""
    result = await db.scalar(
        select(func.count()).select_from(Member).where(Member.system_id == system_id)
    )
    return result or 0


async def enforce_import_member_cap(
    db: AsyncSession,
    system: System,
    incoming: int,
) -> None:
    """Raise ImportPayloadError if adding `incoming` new members would push the
    system past its member cap.

    No-op when the account is unlimited (limit 0) or the import adds nothing.
    Raising ImportPayloadError gets it surfaced to the user as a clean job
    failure (a classified, expected failure) rather than an unhandled error.
    Imports are additive, so `incoming` is counted on top of the existing
    members.
    """
    if incoming <= 0:
        return
    user = await db.get(User, system.user_id)
    if user is None:
        return
    limit = get_member_limit(user)
    if limit <= 0:
        return
    current = await count_members(db, system.id)
    if current + incoming > limit:
        over = current + incoming - limit
        raise ImportPayloadError(
            f"This import would add {incoming} members, but your account is "
            f"limited to {limit} ({current} already in use) — {over} over the "
            "cap. Deselect at least that many members and try again, or upgrade "
            "for a higher limit."
        )

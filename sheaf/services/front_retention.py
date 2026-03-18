"""Front history retention / pruning for aaS free tier.

In self-hosted mode this module is never invoked. In aaS mode it runs
as a periodic task to prune front history older than the configured
retention window for free-tier users.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import SheafMode, settings
from sheaf.models.front import Front
from sheaf.models.member import front_members
from sheaf.models.system import System
from sheaf.models.user import User, UserTier

logger = logging.getLogger("sheaf.retention")


async def prune_free_tier_fronts(db: AsyncSession) -> int:
    """Delete front history older than the retention window for free-tier users.

    Returns the number of fronts deleted.
    """
    if settings.sheaf_mode != SheafMode.SAAS:
        logger.debug("Skipping pruning — not in aaS mode")
        return 0

    cutoff = datetime.now(UTC) - timedelta(days=settings.free_tier_front_retention_days)

    # Find systems owned by free-tier users
    free_system_ids = (
        select(System.id)
        .join(User, System.user_id == User.id)
        .where(User.tier == UserTier.FREE)
        .scalar_subquery()
    )

    # Find fronts to delete
    old_fronts = (
        select(Front.id)
        .where(
            Front.system_id.in_(free_system_ids),
            Front.started_at < cutoff,
            Front.ended_at.is_not(None),  # Never prune open fronts
        )
        .scalar_subquery()
    )

    # Delete junction table rows first
    await db.execute(
        delete(front_members).where(front_members.c.front_id.in_(old_fronts))
    )

    # Delete fronts
    result = await db.execute(
        delete(Front).where(Front.id.in_(old_fronts))
    )

    count = result.rowcount
    if count > 0:
        logger.info("Pruned %d front records older than %s", count, cutoff.isoformat())

    return count

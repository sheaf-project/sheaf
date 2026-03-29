"""Front history retention / pruning for aaS free tier.

In self-hosted mode this module is never invoked. In aaS mode it runs
as a periodic task to prune front history older than the configured
retention window for free-tier users.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import SheafMode, settings
from sheaf.models.front import Front
from sheaf.models.member import front_members
from sheaf.models.system import System
from sheaf.models.user import User, UserTier

logger = logging.getLogger("sheaf.retention")


async def prune_free_tier_fronts(db: AsyncSession) -> dict:
    """Delete front history older than the retention window for free-tier users.

    Returns dict with items_processed count and per-user detail.
    """
    if settings.sheaf_mode != SheafMode.SAAS:
        logger.debug("Skipping pruning — not in aaS mode")
        return {"items_processed": 0}

    cutoff = datetime.now(UTC) - timedelta(days=settings.free_tier_front_retention_days)

    # Find systems owned by free-tier users
    free_system_ids = (
        select(System.id)
        .join(User, System.user_id == User.id)
        .where(User.tier == UserTier.FREE)
        .scalar_subquery()
    )

    # Count per-user before deleting (for detail log)
    per_user_counts = await db.execute(
        select(System.user_id, func.count(Front.id))
        .join(System, Front.system_id == System.id)
        .where(
            Front.system_id.in_(free_system_ids),
            Front.started_at < cutoff,
            Front.ended_at.is_not(None),
        )
        .group_by(System.user_id)
    )
    user_counts = {uid: cnt for uid, cnt in per_user_counts.all()}

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

    detail_lines = [
        f"User {uid}: pruned {cnt} fronts" for uid, cnt in user_counts.items()
    ]

    return {
        "items_processed": count,
        "details": "\n".join(detail_lines) if detail_lines else None,
    }

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
from sheaf.models.activity_event import ActivityAction, ActivityActorType
from sheaf.models.front import Front
from sheaf.models.member import front_members
from sheaf.models.system import System
from sheaf.models.user import User, UserTier
from sheaf.services.activity_log import log_activity

logger = logging.getLogger("sheaf.retention")


async def prune_free_tier_fronts(db: AsyncSession) -> dict:
    """Delete closed, long-dormant front history for free-tier users.

    A front is pruned IFF all three hold together:

      - ``created_at < cutoff`` - the row was *inserted* long ago. This is the
        row-landing time (set to NOW on every insert), not the front's
        real-world ``started_at``. It protects freshly-imported historical
        fronts: an import carries old ``started_at`` / ``ended_at`` values but
        lands with ``created_at`` = now, so it is never eligible until it has
        actually lived in this database for the full window. Keying off
        ``started_at`` (the original incident bug) deleted just-imported
        history; keying off ``ended_at`` alone does not fix it, because
        imported fronts also carry an old ``ended_at``.
      - ``ended_at IS NOT NULL`` - the front is closed. Open fronts are never
        pruned while ongoing.
      - ``ended_at < cutoff`` - it also *ended* long ago. This protects a
        genuinely long-running native front (months, years) that was created
        long ago but only just closed: its most recent activity is recent, so
        it is kept for the full window after it ends rather than vanishing the
        instant it closes.

    Returns dict with items_processed count and per-user detail.
    """
    if settings.sheaf_mode != SheafMode.SAAS:
        logger.debug("Skipping pruning - not in aaS mode")
        return {"items_processed": 0}

    cutoff = datetime.now(UTC) - timedelta(days=settings.free_tier_front_retention_days)

    # Find systems owned by free-tier users
    free_system_ids = (
        select(System.id)
        .join(User, System.user_id == User.id)
        .where(User.tier == UserTier.FREE)
        .scalar_subquery()
    )

    # Count per-user before deleting (for detail log). Predicate mirrors the
    # delete subquery below exactly - see the docstring for why all three
    # clauses are required (created_at guards imported history, ended_at guards
    # recently-active long fronts, IS NOT NULL never touches open fronts).
    per_user_counts = await db.execute(
        select(System.user_id, func.count(Front.id))
        .join(System, Front.system_id == System.id)
        .where(
            Front.system_id.in_(free_system_ids),
            Front.created_at < cutoff,  # Inserted long ago (protects imports)
            Front.ended_at.is_not(None),  # Never prune open fronts
            Front.ended_at < cutoff,  # Ended long ago (protects recent activity)
        )
        .group_by(System.user_id)
    )
    user_counts = {uid: cnt for uid, cnt in per_user_counts.all()}

    # Find fronts to delete
    old_fronts = (
        select(Front.id)
        .where(
            Front.system_id.in_(free_system_ids),
            Front.created_at < cutoff,  # Inserted long ago (protects imports)
            Front.ended_at.is_not(None),  # Never prune open fronts
            Front.ended_at < cutoff,  # Ended long ago (protects recent activity)
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

    # Nothing-silent: leave a per-user account-activity trace whenever a prune
    # actually removes rows for that user. The incident was hidden precisely
    # because the sweep deleted with no trail. Content-free - counts only, no
    # member content. This needs a new ActivityAction ("retention_pruned") plus
    # its Postgres enum migration; until both land the lookup returns None and
    # emission is a no-op (these jobs stay paused until re-enabled). See the
    # follow-up flagged in the change report.
    retention_action = getattr(ActivityAction, "RETENTION_PRUNED", None)
    if retention_action is not None:
        for uid, cnt in user_counts.items():
            if cnt:
                await log_activity(
                    db,
                    user_id=uid,
                    action=retention_action,
                    actor_type=ActivityActorType.SYSTEM,
                    detail={"fronts_pruned": cnt},
                )

    detail_lines = [
        f"User {uid}: pruned {cnt} fronts" for uid, cnt in user_counts.items()
    ]

    return {
        "items_processed": count,
        "details": "\n".join(detail_lines) if detail_lines else None,
    }

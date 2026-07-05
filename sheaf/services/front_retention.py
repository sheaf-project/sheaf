"""User-opt-in front-history privacy retention sweep.

A person may not want a multi-year fronting record sitting around, so a
per-system setting (``System.front_retention_days``, 0 = off) lets them age
out their own closed fronting history. This is the only legitimate reason to
delete someone's identity data, and it is theirs to choose - so unlike the
operator cost-control pruner this replaces, it is NOT mode-gated: it applies in
self-hosted deployments too.

A front is pruned IFF ALL of these hold together (the design's "Option B"
predicate):

  - its owning system opted in (``front_retention_days`` > 0),
  - it is closed (``ended_at`` IS NOT NULL) - open fronts are never pruned
    while ongoing,
  - it ended long ago in the real world (``ended_at`` < now minus the system's
    own ``front_retention_days`` window), because the user's intent is "no
    fronting record of me older than X exists", a statement about when the
    fronting happened, and
  - it has actually lived in this database past a fixed import grace
    (``created_at`` < now minus ``front_retention_import_grace_days``). An
    imported front carries its old real-world ``ended_at``, so a system that
    turns retention on and imports two years of history has just landed a pile
    of already-eligible rows; the grace means freshly-imported old history is
    never abruptly deleted - the user has a couple of weeks to review it and
    change their mind. This is the clause that fixed a retention bug (caught
    and fixed the same day, no data lost).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.models.activity_event import ActivityAction, ActivityActorType
from sheaf.models.front import Front
from sheaf.models.member import front_members
from sheaf.models.system import System
from sheaf.services.activity_log import log_activity

logger = logging.getLogger("sheaf.retention")

# Delete in bounded batches with a commit between each rather than one giant
# DELETE, so a large opt-in system's backlog can't hold a single long
# transaction / lock set. The loop re-runs the predicate until nothing is left.
_SWEEP_BATCH_SIZE = 5000


async def sweep_front_retention(db: AsyncSession) -> dict:
    """Age out closed, long-ended fronting history for opted-in systems.

    Deletes fronts (and their ``front_members`` junction rows) matching the
    Option B predicate above, in batches with a per-batch commit. Not
    mode-gated - this is a user privacy control, so it runs everywhere.

    Nothing-silent: computes per-user counts before the delete loop and, after
    deleting, leaves one content-free ``RETENTION_PRUNED`` account-activity
    trace per affected user (detail ``{"fronts_pruned": n}``, actor SYSTEM),
    mirroring the poll purge and revision GC. A sweep that deletes with no
    trail is invisible until someone reads the job internals.

    Returns dict with items_processed count and per-user detail.
    """
    now = datetime.now(UTC)
    # Fixed import grace: bound as a Python datetime cutoff (simpler than an SQL
    # interval for a constant), unlike the per-system window below.
    import_cutoff = now - timedelta(days=settings.front_retention_import_grace_days)

    # Shared eligibility predicate. The per-system window is expressed in SQL by
    # joining fronts to systems and running make_interval on each system's own
    # integer front_retention_days, exactly as purge_expired_polls does with the
    # per-poll retention_days - so the window is per-system without hardcoding a
    # day count.
    eligible = (
        System.front_retention_days > 0,  # opted in
        Front.ended_at.is_not(None),  # closed; never prune open fronts
        # Ended long ago in the real world, per that system's own window.
        Front.ended_at < now - func.make_interval(0, 0, 0, System.front_retention_days),
        Front.created_at < import_cutoff,  # not freshly imported
    )

    # Per-user counts, taken before the delete loop so the activity trace
    # matches what was actually removed.
    per_user = await db.execute(
        select(System.user_id, func.count(Front.id))
        .join(System, Front.system_id == System.id)
        .where(*eligible)
        .group_by(System.user_id)
    )
    user_counts = {uid: cnt for uid, cnt in per_user.all()}

    # Batched delete loop. Each pass materialises up to _SWEEP_BATCH_SIZE
    # eligible front ids into a Python list (so the junction delete and the
    # front delete act on exactly the same rows - a LIMIT subquery re-evaluated
    # per statement, with no ORDER BY, could otherwise pick different rows for
    # each), deletes their junction rows first, then the fronts, and commits.
    # Deleted rows drop out of the predicate, so the next pass picks up the
    # following batch until none remain.
    total = 0
    while True:
        batch = (
            (
                await db.execute(
                    select(Front.id)
                    .join(System, Front.system_id == System.id)
                    .where(*eligible)
                    .limit(_SWEEP_BATCH_SIZE)
                )
            )
            .scalars()
            .all()
        )
        if not batch:
            break

        # front_members has DB-level ON DELETE CASCADE on front_id, but delete
        # the junction rows explicitly first to be safe (mirrors the retired
        # pruner) rather than relying solely on the cascade.
        await db.execute(
            delete(front_members).where(front_members.c.front_id.in_(batch))
        )
        result = await db.execute(delete(Front).where(Front.id.in_(batch)))
        await db.commit()

        total += result.rowcount or 0
        if len(batch) < _SWEEP_BATCH_SIZE:
            break

    if total > 0:
        logger.info("Front-retention sweep pruned %d closed fronts", total)

    # Nothing-silent: one per-user trace per user whose rows were removed.
    # Content-free - counts only, no fronting content.
    for uid, cnt in user_counts.items():
        if cnt:
            await log_activity(
                db,
                user_id=uid,
                action=ActivityAction.RETENTION_PRUNED,
                actor_type=ActivityActorType.SYSTEM,
                detail={"fronts_pruned": cnt},
            )

    detail_lines = [
        f"User {uid}: pruned {cnt} fronts" for uid, cnt in user_counts.items() if cnt
    ]

    return {
        "items_processed": total,
        "details": "\n".join(detail_lines) if detail_lines else None,
    }

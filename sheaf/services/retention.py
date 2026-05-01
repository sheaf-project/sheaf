"""Revision-history retention service.

Three responsibilities:
- `on_tier_change` — triggered by future billing/admin paths when User.tier
  is mutated. Creates a RetentionTrimNotice if downgrading to lower caps,
  cancels any pending notice on upgrade.
- `effective_caps_with_grace` — what caps the GC sweep should currently honor,
  accounting for an active downgrade notice.
- `gc_revisions` — the periodic job that trims content_revisions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.models.content_revision import ContentRevision
from sheaf.models.retention_trim_notice import (
    RetentionTrimNotice,
    RetentionTrimStatus,
)
from sheaf.models.system import System
from sheaf.models.user import User, UserTier
from sheaf.services.journals import (
    _combine_cap,
    effective_revision_caps,
    tier_revision_caps,
)

logger = logging.getLogger("sheaf.retention")


# ---------------------------------------------------------------------------
# Active-notice helpers
# ---------------------------------------------------------------------------


async def get_active_trim_notice(
    user_id: uuid.UUID, db: AsyncSession
) -> RetentionTrimNotice | None:
    """Return the user's pending trim notice, if any."""
    result = await db.execute(
        select(RetentionTrimNotice).where(
            RetentionTrimNotice.user_id == user_id,
            RetentionTrimNotice.status == RetentionTrimStatus.PENDING,
        )
    )
    return result.scalars().first()


def _caps_for_tier_with_overrides(
    tier: UserTier | str, system: System
) -> tuple[int, int]:
    """Compute effective caps for a hypothetical tier, applying system overrides."""
    tier_rev, tier_days = tier_revision_caps(tier)
    return (
        _combine_cap(tier_rev, system.journal_max_revisions),
        _combine_cap(tier_days, system.journal_max_revision_days),
    )


def _max_cap(a: int, b: int) -> int:
    """Return the higher of two caps, where 0 means unlimited."""
    if a == 0 or b == 0:
        return 0
    return max(a, b)


async def effective_caps_with_grace(
    user: User, system: System, db: AsyncSession
) -> tuple[int, int, RetentionTrimNotice | None]:
    """Caps the GC sweep should honor right now.

    During an active downgrade-grace window, we use the higher of the
    pre-downgrade caps and the current caps so we don't trim ahead of the
    user-visible deadline. Once `effective_at` has passed, the notice is
    no longer honored (caller marks it completed).
    """
    notice = await get_active_trim_notice(user.id, db)
    current_rev, current_days = effective_revision_caps(user, system)
    if notice is None or notice.effective_at <= datetime.now(UTC):
        return current_rev, current_days, notice

    pre_rev, pre_days = _caps_for_tier_with_overrides(notice.from_tier, system)
    return (
        _max_cap(current_rev, pre_rev),
        _max_cap(current_days, pre_days),
        notice,
    )


# ---------------------------------------------------------------------------
# Tier change trigger
# ---------------------------------------------------------------------------


async def on_tier_change(
    user: User,
    old_tier: UserTier | str,
    new_tier: UserTier | str,
    db: AsyncSession,
) -> RetentionTrimNotice | None:
    """Create a trim notice if the new tier reduces effective caps.

    Idempotent: cancels any pending notice for this user before deciding.
    No-op if the change is a no-op or an upgrade. Caller commits.

    NOTE: v1 has no caller — billing/admin paths land later. This function
    ships unused so the trigger surface is in place when those paths arrive.
    """
    old_tier_e = UserTier(old_tier) if not isinstance(old_tier, UserTier) else old_tier
    new_tier_e = UserTier(new_tier) if not isinstance(new_tier, UserTier) else new_tier

    # Cancel any prior pending notice. An upgrade path stops here; a
    # downgrade path may then create a fresh one with the latest from-tier.
    existing = await get_active_trim_notice(user.id, db)
    if existing is not None:
        existing.status = RetentionTrimStatus.CANCELLED
        existing.cancelled_at = datetime.now(UTC)

    if old_tier_e == new_tier_e:
        return None

    # If the user has no system yet, nothing to gate on.
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        return None

    old_rev, old_days = _caps_for_tier_with_overrides(old_tier_e, system)
    new_rev, new_days = _caps_for_tier_with_overrides(new_tier_e, system)

    # 0 = unlimited. A downgrade can only reduce a non-zero cap or replace
    # unlimited with a finite cap.
    def is_reduction(old_cap: int, new_cap: int) -> bool:
        if new_cap == 0:
            return False  # new tier is unlimited — never a reduction
        if old_cap == 0:
            return True  # was unlimited, now finite
        return new_cap < old_cap

    if not (is_reduction(old_rev, new_rev) or is_reduction(old_days, new_days)):
        return None

    now = datetime.now(UTC)
    notice = RetentionTrimNotice(
        user_id=user.id,
        requested_at=now,
        effective_at=now + timedelta(days=settings.tier_downgrade_grace_days),
        from_tier=str(old_tier_e),
        to_tier=str(new_tier_e),
        reason="tier_downgrade",
        status=RetentionTrimStatus.PENDING,
    )
    db.add(notice)
    return notice


# ---------------------------------------------------------------------------
# GC sweep
# ---------------------------------------------------------------------------


async def _trim_target_group(
    *,
    db: AsyncSession,
    target_type: str,
    target_id: uuid.UUID,
    max_revisions: int,
    max_days: int,
) -> int:
    """Trim revisions for a single target. Returns the number deleted.

    `max_revisions=0` means unlimited (no count cap).
    `max_days=0` means unlimited (no age cap).

    Pinned revisions (pinned_at IS NOT NULL) are exempt from both caps. They
    form a separate budget bounded by the per-target pin cap, not this sweep.
    """
    if max_revisions == 0 and max_days == 0:
        return 0

    result = await db.execute(
        select(ContentRevision)
        .where(
            ContentRevision.target_type == target_type,
            ContentRevision.target_id == target_id,
            ContentRevision.pinned_at.is_(None),
        )
        .order_by(ContentRevision.created_at.desc())
    )
    rows = list(result.scalars().all())
    if not rows:
        return 0

    # Keep up to max_revisions newest if a count cap exists.
    keep: set[uuid.UUID] = (
        {r.id for r in rows[:max_revisions]} if max_revisions > 0 else {r.id for r in rows}
    )

    # Then drop any kept rows older than max_days.
    if max_days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=max_days)
        keep = {r.id for r in rows if r.id in keep and r.created_at >= cutoff}

    to_delete = [r.id for r in rows if r.id not in keep]
    if not to_delete:
        return 0

    await db.execute(
        delete(ContentRevision).where(ContentRevision.id.in_(to_delete))
    )
    return len(to_delete)


async def gc_revisions(db: AsyncSession) -> dict:
    """Periodic job: trim each user's revision history to their effective caps.

    Honors active RetentionTrimNotice during its grace window. Marks any
    notices whose `effective_at` has passed as completed after the sweep.
    """
    # Iterate over every user that owns a system, since both the tier and
    # the override live across the user/system pair. We collect targets
    # (target_type, target_id) by joining content_revisions to the appropriate
    # owner column at the application layer because the table is polymorphic.
    result = await db.execute(
        select(User, System).join(System, System.user_id == User.id)
    )
    pairs = list(result.all())

    total_deleted = 0
    detail_lines: list[str] = []
    completed_notices = 0

    from sheaf.models.journal_entry import JournalEntry
    from sheaf.models.member import Member

    for user, system in pairs:
        max_rev, max_days, notice = await effective_caps_with_grace(user, system, db)

        # Collect (target_type, target_id) tuples owned by this user/system.
        targets: list[tuple[str, uuid.UUID]] = []

        je_result = await db.execute(
            select(JournalEntry.id).where(JournalEntry.system_id == system.id)
        )
        for (je_id,) in je_result.all():
            targets.append(("journal_entry", je_id))

        m_result = await db.execute(
            select(Member.id).where(Member.system_id == system.id)
        )
        for (m_id,) in m_result.all():
            targets.append(("member_bio", m_id))

        user_deleted = 0
        for target_type, target_id in targets:
            user_deleted += await _trim_target_group(
                db=db,
                target_type=target_type,
                target_id=target_id,
                max_revisions=max_rev,
                max_days=max_days,
            )

        if user_deleted:
            total_deleted += user_deleted
            detail_lines.append(
                f"User {user.id}: trimmed {user_deleted} revisions "
                f"(caps: {max_rev} count, {max_days} days)"
            )

        # Mark the notice completed if its window has passed.
        if notice is not None and notice.effective_at <= datetime.now(UTC):
            notice.status = RetentionTrimStatus.COMPLETED
            notice.completed_at = datetime.now(UTC)
            completed_notices += 1

    if completed_notices:
        detail_lines.append(f"Completed {completed_notices} trim notices")

    # Sweep up any orphaned revisions whose target was deleted without going
    # through the cascade helper (defensive). target_type is polymorphic so
    # there's no FK to lean on.
    orphan_journal = await _delete_orphaned_revisions_for(
        "journal_entry", JournalEntry, db
    )
    orphan_bio = await _delete_orphaned_revisions_for("member_bio", Member, db)
    if orphan_journal or orphan_bio:
        total_deleted += orphan_journal + orphan_bio
        detail_lines.append(
            f"Orphan sweep: {orphan_journal} journal-entry, {orphan_bio} member-bio revisions"
        )

    return {
        "items_processed": total_deleted,
        "details": "\n".join(detail_lines) if detail_lines else None,
    }


async def _delete_orphaned_revisions_for(
    target_type: str, target_model: type, db: AsyncSession
) -> int:
    """Drop revisions whose target row no longer exists."""
    # Collect orphaned target_ids in one query.
    sub = select(target_model.id)
    result = await db.execute(
        select(ContentRevision.id).where(
            and_(
                ContentRevision.target_type == target_type,
                ContentRevision.target_id.notin_(sub),
            )
        )
    )
    ids = [row[0] for row in result.all()]
    if not ids:
        return 0
    await db.execute(delete(ContentRevision).where(ContentRevision.id.in_(ids)))
    return len(ids)

"""Journals + revision-history service layer.

Lifecycle helpers for journal entries, polymorphic content revisions, and
the tier-aware retention-cap lookup. Wiring (HTTP + safety dispatch) lives
in `sheaf/api/v1/journals.py` and `sheaf/services/system_safety.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.models.content_revision import ContentRevision, ContentRevisionTarget
from sheaf.models.journal_entry import JournalEntry
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.user import User, UserTier
from sheaf.services.markdown import extract_image_keys
from sheaf.services.system_safety import snapshot_current_fronts

# ---------------------------------------------------------------------------
# Tier-cap helpers
# ---------------------------------------------------------------------------

_TIER_REVISION_CAP = {
    UserTier.FREE: lambda: settings.journal_max_revisions_free,
    UserTier.PLUS: lambda: settings.journal_max_revisions_plus,
    UserTier.SELF_HOSTED: lambda: settings.journal_max_revisions_selfhosted,
}

_TIER_DAY_CAP = {
    UserTier.FREE: lambda: settings.journal_max_revision_days_free,
    UserTier.PLUS: lambda: settings.journal_max_revision_days_plus,
    UserTier.SELF_HOSTED: lambda: settings.journal_max_revision_days_selfhosted,
}


def tier_revision_caps(tier: UserTier | str) -> tuple[int, int]:
    """Return (max_revisions, max_days) for a tier. 0 means unlimited."""
    # Accept the StrEnum or its underlying string.
    key = UserTier(tier) if not isinstance(tier, UserTier) else tier
    return (
        _TIER_REVISION_CAP.get(key, lambda: 0)(),
        _TIER_DAY_CAP.get(key, lambda: 0)(),
    )


def _combine_cap(tier_cap: int, override: int | None) -> int:
    """Combine tier max + system override. 0 = unlimited on either side.

    The override is honored only if it is <= the tier max (lower or equal).
    Higher overrides are ignored (the tier max wins).
    """
    if override is None:
        return tier_cap
    if tier_cap == 0:
        # Unlimited tier: any concrete override applies.
        return override
    if override == 0:
        # Override means "unlimited" but tier caps it.
        return tier_cap
    return min(override, tier_cap)


def effective_revision_caps(user: User, system: System) -> tuple[int, int]:
    """Return (max_revisions, max_days) actually in force for this user/system."""
    tier_rev, tier_days = tier_revision_caps(user.tier)
    return (
        _combine_cap(tier_rev, system.journal_max_revisions),
        _combine_cap(tier_days, system.journal_max_revision_days),
    )


# ---------------------------------------------------------------------------
# Revision capture / cascade
# ---------------------------------------------------------------------------


async def capture_revision(
    *,
    db: AsyncSession,
    target_type: ContentRevisionTarget | str,
    target_id: uuid.UUID,
    user: User,
    system_id: uuid.UUID,
    title: str | None,
    body: str,
) -> ContentRevision:
    """Insert a content_revisions row capturing the *outgoing* content.

    Caller is responsible for then overwriting the target row with the new
    content and committing.
    """
    target_type_str = (
        target_type.value if isinstance(target_type, ContentRevisionTarget) else target_type
    )
    editor_ids, editor_names = await snapshot_current_fronts(system_id, db)
    revision = ContentRevision(
        target_type=target_type_str,
        target_id=target_id,
        user_id=user.id,
        editor_member_ids=editor_ids,
        editor_member_names=editor_names,
        title=title,
        body=body,
        image_keys=extract_image_keys(body),
    )
    db.add(revision)
    return revision


async def delete_revisions_for(
    target_type: ContentRevisionTarget | str,
    target_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """App-level cascade: drop all revisions for a deleted target.

    Polymorphic FK can't be enforced at DB level, so callers must call this
    before deleting the target row (or in the same transaction).
    """
    target_type_str = (
        target_type.value if isinstance(target_type, ContentRevisionTarget) else target_type
    )
    await db.execute(
        delete(ContentRevision).where(
            ContentRevision.target_type == target_type_str,
            ContentRevision.target_id == target_id,
        )
    )


async def revision_count_for(
    target_type: ContentRevisionTarget | str,
    target_id: uuid.UUID,
    db: AsyncSession,
) -> int:
    target_type_str = (
        target_type.value if isinstance(target_type, ContentRevisionTarget) else target_type
    )
    from sqlalchemy import func

    result = await db.execute(
        select(func.count())
        .select_from(ContentRevision)
        .where(
            ContentRevision.target_type == target_type_str,
            ContentRevision.target_id == target_id,
        )
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Author resolution
# ---------------------------------------------------------------------------


async def resolve_author_snapshot(
    member_ids: list[uuid.UUID],
    system_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[list[str], list[str]]:
    """Validate member IDs belong to this system, return parallel id/name lists.

    Raises ValueError if any ID is unknown or belongs to another system. Order
    follows the order of `member_ids`. Duplicate IDs are deduped (first wins).
    """
    seen: set[uuid.UUID] = set()
    ordered: list[uuid.UUID] = []
    for mid in member_ids:
        if mid not in seen:
            seen.add(mid)
            ordered.append(mid)
    if not ordered:
        return [], []
    result = await db.execute(
        select(Member).where(Member.id.in_(ordered), Member.system_id == system_id)
    )
    by_id = {m.id: m for m in result.scalars().all()}
    missing = [str(mid) for mid in ordered if mid not in by_id]
    if missing:
        raise ValueError(f"Unknown member id(s): {', '.join(missing)}")
    ids: list[str] = []
    names: list[str] = []
    for mid in ordered:
        member = by_id[mid]
        ids.append(str(member.id))
        names.append(member.display_name or member.name)
    return ids, names


# ---------------------------------------------------------------------------
# Journal entry create
# ---------------------------------------------------------------------------


async def create_journal_entry(
    *,
    db: AsyncSession,
    user: User,
    system: System,
    member_id: uuid.UUID | None,
    title: str | None,
    body: str,
    visibility: str = "system",
    author_member_ids: list[uuid.UUID] | None = None,
) -> JournalEntry:
    """Create a journal entry with a fronting snapshot at write time.

    If `author_member_ids` is provided, those override the fronting snapshot
    (used when the user explicitly picks authors in the UI). Otherwise we
    snapshot whoever is currently fronting.

    Caller commits + refreshes.
    """
    if author_member_ids is not None:
        author_ids, author_names = await resolve_author_snapshot(
            author_member_ids, system.id, db
        )
    else:
        author_ids, author_names = await snapshot_current_fronts(system.id, db)
    entry = JournalEntry(
        system_id=system.id,
        member_id=member_id,
        title=title,
        body=body,
        visibility=visibility,
        author_user_id=user.id,
        author_member_ids=author_ids,
        author_member_names=author_names,
        image_keys=extract_image_keys(body),
    )
    db.add(entry)
    return entry


async def update_journal_entry(
    *,
    db: AsyncSession,
    user: User,
    entry: JournalEntry,
    title: str | None,
    body: str | None,
    visibility: str | None,
    author_member_ids: list[uuid.UUID] | None = None,
) -> JournalEntry:
    """Apply an update to an entry, capturing a revision if content changed.

    Author edits don't trigger revision capture — revisions track title/body
    only. An empty list (`[]`) clears authors back to "account fallback".
    `None` means "don't touch authors".
    """
    content_changed = (title is not None and title != entry.title) or (
        body is not None and body != entry.body
    )
    if content_changed:
        await capture_revision(
            db=db,
            target_type=ContentRevisionTarget.JOURNAL_ENTRY,
            target_id=entry.id,
            user=user,
            system_id=entry.system_id,
            title=entry.title,
            body=entry.body,
        )
    if title is not None:
        entry.title = title
    if body is not None:
        entry.body = body
        entry.image_keys = extract_image_keys(body)
    if visibility is not None:
        entry.visibility = visibility
    if author_member_ids is not None:
        ids, names = await resolve_author_snapshot(
            author_member_ids, entry.system_id, db
        )
        entry.author_member_ids = ids
        entry.author_member_names = names
    entry.updated_at = datetime.now(UTC)
    return entry


async def restore_journal_revision(
    *,
    db: AsyncSession,
    user: User,
    entry: JournalEntry,
    revision: ContentRevision,
) -> JournalEntry:
    """Restore an entry from a revision.

    Captures the current (pre-restore) content as a new revision first, then
    overwrites the entry with the chosen revision's content. The chosen
    revision row is left in place — restore is a forward action, not a rewind.
    """
    await capture_revision(
        db=db,
        target_type=ContentRevisionTarget.JOURNAL_ENTRY,
        target_id=entry.id,
        user=user,
        system_id=entry.system_id,
        title=entry.title,
        body=entry.body,
    )
    entry.title = revision.title
    entry.body = revision.body
    entry.image_keys = extract_image_keys(revision.body)
    entry.updated_at = datetime.now(UTC)
    return entry

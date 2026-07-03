"""Journals + revision-history service layer.

Lifecycle helpers for journal entries, polymorphic content revisions, and
the tier-aware retention-cap lookup. Wiring (HTTP + safety dispatch) lives
in `sheaf/api/v1/journals.py` and `sheaf/services/system_safety.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.crypto import decrypt, encrypt
from sheaf.models.content_revision import ContentRevision, ContentRevisionTarget
from sheaf.models.journal_entry import JournalEntry
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.user import User, UserTier
from sheaf.observability.metrics import content_revisions_created_total
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


async def _latest_revision(
    target_type_str: str,
    target_id: uuid.UUID,
    db: AsyncSession,
) -> ContentRevision | None:
    """Return the newest revision for a target by inserted_at, or None.

    "Newest" is by ``inserted_at`` (true row-landing time), matching the
    ordering the retention sweep and the write-time count cap use, so the
    debounce window is measured against the same clock the trim honors.
    """
    result = await db.execute(
        select(ContentRevision)
        .where(
            ContentRevision.target_type == target_type_str,
            ContentRevision.target_id == target_id,
        )
        .order_by(ContentRevision.inserted_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _enforce_write_time_count_cap(
    *,
    db: AsyncSession,
    target_type_str: str,
    target_id: uuid.UUID,
    max_revisions: int,
) -> None:
    """Trim UNPINNED revisions past the count cap for a single target.

    Runs at write time so the periodic GC sweep is a backstop rather than the
    primary enforcement. Keeps the newest ``max_revisions`` unpinned rows by
    ``inserted_at`` (matching the sweep's ordering) and deletes the older
    overflow in one bounded DELETE. Pinned revisions (pinned_at IS NOT NULL)
    are never counted or deleted. ``max_revisions == 0`` means unlimited.

    The keep-set subquery is bounded to ``max_revisions`` rows, so this stays
    O(cap)-ish rather than loading the whole history into Python.
    """
    if max_revisions <= 0:
        return
    keep_ids = (
        select(ContentRevision.id)
        .where(
            ContentRevision.target_type == target_type_str,
            ContentRevision.target_id == target_id,
            ContentRevision.pinned_at.is_(None),
        )
        .order_by(ContentRevision.inserted_at.desc())
        .limit(max_revisions)
    )
    await db.execute(
        delete(ContentRevision).where(
            ContentRevision.target_type == target_type_str,
            ContentRevision.target_id == target_id,
            ContentRevision.pinned_at.is_(None),
            ContentRevision.id.not_in(keep_ids),
        )
    )


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

    `title` and `body` are *plaintext* - encrypted at write time.
    image_keys is extracted from the plaintext body, then stored unencrypted
    so orphan cleanup can read it without key access.

    Debounce (checkpoint semantics): when `revision_debounce_minutes` is
    enabled and the target's newest revision is UNPINNED and landed within
    that window, we REPLACE that revision's captured content in place (title,
    body, image_keys, and the editor snapshot) instead of appending a new row.
    `inserted_at` is deliberately NOT refreshed on replace: the window stays
    anchored to when each checkpoint was born, so a burst of rapid saves
    collapses into one revision while a long editing session still accrues a
    fresh checkpoint roughly every `revision_debounce_minutes` (the first save
    after the window elapses appends again). If the newest revision is pinned,
    older than the window, missing (first-ever edit), or debounce is disabled,
    we append a new row as before.

    If this is the first revision captured for the target AND the system has
    `auto_pin_first_revision=True`, the new row is pinned. Defends against
    spam-eviction even when the destructive-action grace flow is off.

    After the write, the effective per-target count cap is enforced (oldest
    unpinned overflow trimmed); this makes the GC sweep a backstop. Pinned
    revisions are exempt.

    Caller is responsible for then overwriting the target row with the new
    content and committing.
    """
    target_type_str = (
        target_type.value if isinstance(target_type, ContentRevisionTarget) else target_type
    )
    editor_ids, editor_names = await snapshot_current_fronts(system_id, db)

    system = await db.get(System, system_id)
    max_revisions = (
        effective_revision_caps(user, system)[0] if system is not None else 0
    )

    enc_title = encrypt(title) if title is not None else None
    enc_body = encrypt(body)
    image_keys = extract_image_keys(body)

    debounce_minutes = settings.revision_debounce_minutes
    if debounce_minutes > 0:
        latest = await _latest_revision(target_type_str, target_id, db)
        if (
            latest is not None
            and latest.pinned_at is None
            and _within_debounce_window(latest.inserted_at, debounce_minutes)
        ):
            # Replace the newest checkpoint in place. Reuse the same encrypt()
            # calls as the append path so nothing plaintext is ever stored;
            # image_keys stays the plaintext-extracted list, as today. Leave
            # inserted_at untouched on purpose (see docstring).
            latest.title = enc_title
            latest.body = enc_body
            latest.image_keys = image_keys
            latest.editor_member_ids = editor_ids
            latest.editor_member_names = editor_names
            # No new row, so the cap usually has nothing to trim, but run it
            # anyway - it is cheap and mops up any pre-existing overflow.
            await _enforce_write_time_count_cap(
                db=db,
                target_type_str=target_type_str,
                target_id=target_id,
                max_revisions=max_revisions,
            )
            return latest

    existing_count = await revision_count_for(target_type_str, target_id, db)
    pinned_at: datetime | None = None
    if existing_count == 0 and system is not None and system.auto_pin_first_revision:
        pinned_at = datetime.now(UTC)

    revision = ContentRevision(
        target_type=target_type_str,
        target_id=target_id,
        user_id=user.id,
        editor_member_ids=editor_ids,
        editor_member_names=editor_names,
        title=enc_title,
        body=enc_body,
        image_keys=image_keys,
        pinned_at=pinned_at,
    )
    db.add(revision)
    content_revisions_created_total.inc()
    # Flush so the new row participates in the count-cap query below (and gets
    # its server-default inserted_at), then trim the oldest unpinned overflow.
    await db.flush()
    await _enforce_write_time_count_cap(
        db=db,
        target_type_str=target_type_str,
        target_id=target_id,
        max_revisions=max_revisions,
    )
    return revision


def _within_debounce_window(inserted_at: datetime | None, minutes: int) -> bool:
    """True if `inserted_at` is within `minutes` of now (UTC).

    Guards against a naive datetime (older rows / test fixtures) by assuming
    UTC when tzinfo is absent, so the comparison never raises.
    """
    if inserted_at is None:
        return False
    ref = inserted_at if inserted_at.tzinfo is not None else inserted_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ref) <= timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------


_TIER_PIN_CAP = {
    UserTier.FREE: lambda: settings.pinned_revision_max_per_target_free,
    UserTier.PLUS: lambda: settings.pinned_revision_max_per_target_plus,
    UserTier.SELF_HOSTED: lambda: settings.pinned_revision_max_per_target_selfhosted,
}


def tier_pin_cap(tier: UserTier | str) -> int:
    """Per-target pinned-revision cap for a tier. 0 = unlimited."""
    key = UserTier(tier) if not isinstance(tier, UserTier) else tier
    return _TIER_PIN_CAP.get(key, lambda: 0)()


def effective_pin_cap(user: User, system: System) -> int:
    """Per-target pinned-revision cap actually in force. 0 = unlimited."""
    return _combine_cap(tier_pin_cap(user.tier), system.pinned_revision_max_per_target)


async def count_pinned_for_target(
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
            ContentRevision.pinned_at.is_not(None),
        )
    )
    return int(result.scalar_one())


async def pin_revision(
    *,
    db: AsyncSession,
    user: User,
    system: System,
    revision: ContentRevision,
) -> ContentRevision:
    """Mark a revision as pinned. Raises ValueError if already pinned or at cap."""
    if revision.pinned_at is not None:
        raise ValueError("Revision is already pinned")
    cap = effective_pin_cap(user, system)
    if cap > 0:
        current = await count_pinned_for_target(
            revision.target_type, revision.target_id, db
        )
        if current >= cap:
            raise ValueError(
                f"Pin cap reached ({current}/{cap}) for this target — "
                "unpin one first"
            )
    revision.pinned_at = datetime.now(UTC)
    return revision


def unpin_revision_immediate(revision: ContentRevision) -> ContentRevision:
    """Clear the pin flag in-place. Caller commits."""
    revision.pinned_at = None
    return revision


def revision_plaintext(revision: ContentRevision) -> tuple[str | None, str]:
    """Decrypt a revision's title/body to plaintext."""
    title = decrypt(revision.title) if revision.title is not None else None
    body = decrypt(revision.body) if revision.body else ""
    return title, body


def decrypt_revision_for_read(revision: ContentRevision) -> dict:
    """Build a dict suitable for ContentRevisionRead.model_validate(...)."""
    title, body = revision_plaintext(revision)
    return {
        "id": revision.id,
        "target_type": revision.target_type,
        "target_id": revision.target_id,
        "user_id": revision.user_id,
        "editor_member_ids": revision.editor_member_ids,
        "editor_member_names": revision.editor_member_names,
        "title": title,
        "body": body,
        "image_keys": revision.image_keys,
        "created_at": revision.created_at,
        "pinned_at": revision.pinned_at,
    }


def entry_plaintext(entry: JournalEntry) -> tuple[str | None, str]:
    """Decrypt a journal entry's title/body to plaintext."""
    title = decrypt(entry.title) if entry.title is not None else None
    body = decrypt(entry.body) if entry.body else ""
    return title, body


def decrypt_entry_for_read(entry: JournalEntry) -> dict:
    """Build a dict suitable for JournalEntryRead.model_validate(...)."""
    title, body = entry_plaintext(entry)
    return {
        "id": entry.id,
        "system_id": entry.system_id,
        "member_id": entry.member_id,
        "title": title,
        "body": body,
        "visibility": entry.visibility,
        "author_user_id": entry.author_user_id,
        "author_member_ids": entry.author_member_ids,
        "author_member_names": entry.author_member_names,
        "image_keys": entry.image_keys,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


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
        # display_name is plaintext; name is ciphertext — decrypt for the
        # snapshot. Author-name snapshots are display strings, not lookups,
        # so we store them in plaintext (same as how they're shown to users).
        names.append(member.display_name or decrypt(member.name))
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

    `title` and `body` are *plaintext* — stored encrypted; image_keys is
    extracted from the plaintext and stored unencrypted for orphan cleanup.

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
        title=encrypt(title) if title is not None else None,
        body=encrypt(body),
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

    `title` and `body` are *plaintext* (None = unchanged). Comparison against
    the existing entry decrypts the stored ciphertext so no-op nonce rerolls
    don't trigger spurious revision captures.

    Author edits don't trigger revision capture — revisions track title/body
    only. An empty list (`[]`) clears authors back to "account fallback".
    `None` means "don't touch authors".
    """
    current_title, current_body = entry_plaintext(entry)
    content_changed = (title is not None and title != current_title) or (
        body is not None and body != current_body
    )
    if content_changed:
        await capture_revision(
            db=db,
            target_type=ContentRevisionTarget.JOURNAL_ENTRY,
            target_id=entry.id,
            user=user,
            system_id=entry.system_id,
            title=current_title,
            body=current_body,
        )
    if title is not None:
        entry.title = encrypt(title)
    if body is not None:
        entry.body = encrypt(body)
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
    current_title, current_body = entry_plaintext(entry)
    await capture_revision(
        db=db,
        target_type=ContentRevisionTarget.JOURNAL_ENTRY,
        target_id=entry.id,
        user=user,
        system_id=entry.system_id,
        title=current_title,
        body=current_body,
    )
    revision_title, revision_body = revision_plaintext(revision)
    entry.title = encrypt(revision_title) if revision_title is not None else None
    entry.body = encrypt(revision_body)
    entry.image_keys = extract_image_keys(revision_body)
    entry.updated_at = datetime.now(UTC)
    return entry


async def restore_member_bio_revision(
    *,
    db: AsyncSession,
    user: User,
    member: Member,
    revision: ContentRevision,
) -> Member:
    """Restore a member's bio from a revision.

    Same forward-action semantics as `restore_journal_revision`: captures the
    current bio as a new revision, then overwrites `member.description` with
    the revision body. Image keys for member bios are tracked through the
    revision rows themselves (the member table has no `image_keys` column).
    """
    current_description = (
        decrypt(member.description) if member.description is not None else ""
    )
    await capture_revision(
        db=db,
        target_type=ContentRevisionTarget.MEMBER_BIO,
        target_id=member.id,
        user=user,
        system_id=member.system_id,
        title=None,
        body=current_description,
    )
    _, revision_body = revision_plaintext(revision)
    member.description = encrypt(revision_body) if revision_body else None
    return member

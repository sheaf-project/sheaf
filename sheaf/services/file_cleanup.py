"""Orphaned file cleanup.

Finds uploaded files that are no longer referenced by any avatar_url
or bio image, and deletes them from both storage and the database.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import decrypt
from sheaf.models.content_revision import ContentRevision, ContentRevisionTarget
from sheaf.models.journal_entry import JournalEntry
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User
from sheaf.services.journals import entry_plaintext
from sheaf.services.markdown import extract_image_keys
from sheaf.services.members import member_plaintext
from sheaf.storage import get_storage

logger = logging.getLogger("sheaf.cleanup")


def _key_from_avatar(value: str | None) -> set[str]:
    """Return the storage key from an avatar_url DB field.

    avatar_url stores the storage key directly (e.g. avatars/user_id/uuid.png).
    """
    if value:
        return {value}
    return set()


def _extract_keys_from_markdown(text: str | None) -> set[str]:
    """Extract all hosted image keys from markdown image references."""
    return set(extract_image_keys(text))


async def find_orphaned_files(
    db: AsyncSession,
    user_id: str,
) -> list[UploadedFile]:
    """Find files uploaded by a user that are no longer referenced.

    Returns a list of orphaned UploadedFile rows.
    """
    result = await db.execute(
        select(UploadedFile).where(UploadedFile.user_id == user_id)
    )
    uploaded = list(result.scalars().all())

    if not uploaded:
        return []

    uploaded_keys = {f.key for f in uploaded}

    # Collect all referenced keys for this user
    referenced: set[str] = set()

    # System avatar
    result = await db.execute(
        select(System.avatar_url).join(User).where(User.id == user_id)
    )
    for (avatar_url,) in result:
        referenced.update(_key_from_avatar(avatar_url))

    # Member avatars and bios
    result = await db.execute(
        select(Member.avatar_url, Member.description)
        .join(System)
        .join(User)
        .where(User.id == user_id)
    )
    for avatar_url, description in result:
        referenced.update(_key_from_avatar(avatar_url))
        # Member.description is encrypted at rest; decrypt for the markdown
        # scan. This runs in the app container which has the key.
        if description is not None:
            description = decrypt(description)
        referenced.update(_extract_keys_from_markdown(description))

    # Journal entries for this user's system. image_keys is pre-extracted
    # at write so this is a fast set union, not a markdown re-scan.
    result = await db.execute(
        select(JournalEntry.image_keys)
        .join(System, System.id == JournalEntry.system_id)
        .where(System.user_id == user_id)
    )
    for (keys,) in result:
        if keys:
            referenced.update(keys)

    # Content revisions linked to this user's targets (journal entries +
    # member bios). Polymorphic — narrow by target_id rather than user_id
    # because revisions store user_id only as a SET NULL backref.
    target_ids: set[uuid.UUID] = set()
    je_result = await db.execute(
        select(JournalEntry.id)
        .join(System, System.id == JournalEntry.system_id)
        .where(System.user_id == user_id)
    )
    target_ids.update(row[0] for row in je_result.all())
    m_result = await db.execute(
        select(Member.id)
        .join(System, System.id == Member.system_id)
        .where(System.user_id == user_id)
    )
    target_ids.update(row[0] for row in m_result.all())

    if target_ids:
        rev_result = await db.execute(
            select(ContentRevision.image_keys).where(
                ContentRevision.target_id.in_(target_ids)
            )
        )
        for (keys,) in rev_result:
            if keys:
                referenced.update(keys)

    orphaned_keys = uploaded_keys - referenced
    return [f for f in uploaded if f.key in orphaned_keys]


async def find_file_references(
    db: AsyncSession,
    user_id: str,
    key: str,
) -> list[dict]:
    """Find everywhere a single uploaded-file key is referenced for this user.

    The inverse of find_orphaned_files: rather than collecting every
    referenced key, it attributes one key to the specific entities that use
    it, with user-facing labels. Powers the "where is this image used?" view
    shown before a delete. Computed on demand; there is no persistent
    reference table. An empty list means the file is an orphan.
    """
    refs: list[dict] = []

    sys_result = await db.execute(
        select(System).join(User).where(User.id == user_id)
    )
    system = sys_result.scalar_one_or_none()
    if system is None:
        return refs

    if system.avatar_url == key:
        refs.append({
            "kind": "system_avatar",
            "label": "System avatar",
            "target_type": "system",
            "target_id": str(system.id),
        })

    # Members: avatars + bio image embeds. Names/bios are encrypted, so decrypt
    # via member_plaintext to both scan and label.
    members_result = await db.execute(
        select(Member).where(Member.system_id == system.id)
    )
    members = list(members_result.scalars().all())
    member_name: dict[uuid.UUID, str] = {}
    for m in members:
        name, description = member_plaintext(m)
        member_name[m.id] = name
        if m.avatar_url == key:
            refs.append({
                "kind": "member_avatar",
                "label": f"{name}'s avatar",
                "target_type": "member",
                "target_id": str(m.id),
            })
        if description and key in _extract_keys_from_markdown(description):
            refs.append({
                "kind": "member_bio",
                "label": f"{name}'s bio",
                "target_type": "member",
                "target_id": str(m.id),
            })

    # Journal entries. image_keys is pre-extracted at write time.
    journals_result = await db.execute(
        select(JournalEntry).where(JournalEntry.system_id == system.id)
    )
    journal_label: dict[uuid.UUID, str] = {}
    for e in journals_result.scalars().all():
        title, _ = entry_plaintext(e)
        label = title or "Untitled entry"
        journal_label[e.id] = label
        if e.image_keys and key in e.image_keys:
            refs.append({
                "kind": "journal",
                "label": f"Journal: {label}",
                "target_type": "journal_entry",
                "target_id": str(e.id),
            })

    # Content revisions (edit history) for this user's members + journals. A
    # key can appear in several historical revisions of the same target; list
    # each target once so the view isn't spammed with duplicates.
    target_ids = set(member_name) | set(journal_label)
    if target_ids:
        rev_result = await db.execute(
            select(ContentRevision).where(
                ContentRevision.target_id.in_(target_ids)
            )
        )
        seen_targets: set[uuid.UUID] = set()
        for r in rev_result.scalars().all():
            if not r.image_keys or key not in r.image_keys:
                continue
            if r.target_id in seen_targets:
                continue
            seen_targets.add(r.target_id)
            if r.target_type == ContentRevisionTarget.MEMBER_BIO.value:
                who = member_name.get(r.target_id, "a member")
                label = f"Edit history of {who}'s bio"
            elif r.target_type == ContentRevisionTarget.JOURNAL_ENTRY.value:
                who = journal_label.get(r.target_id, "an entry")
                label = f"Edit history of journal: {who}"
            else:
                label = "Edit history"
            refs.append({
                "kind": "revision",
                "label": label,
                "target_type": r.target_type,
                "target_id": str(r.target_id),
            })

    return refs


async def cleanup_orphaned_files(
    db: AsyncSession,
    user_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Delete orphaned files for a user.

    Removes files from storage and deletes the corresponding DB rows.
    Returns stats about what was (or would be) cleaned up.
    """
    orphaned = await find_orphaned_files(db, user_id)

    if not orphaned:
        return {"orphaned": 0, "freed_bytes": 0, "dry_run": dry_run}

    storage = get_storage()

    if dry_run:
        return {
            "orphaned": len(orphaned),
            "freed_bytes": sum(f.size_bytes for f in orphaned),
            "dry_run": True,
            "keys": [f.key for f in orphaned],
        }

    # Re-scan under the same session right before deleting. If a concurrent
    # write attached one of these files (set an avatar, embedded it in a bio)
    # between the initial find and now, this narrows the window where we'd
    # delete a live reference to effectively nothing. Delete DB rows first
    # and commit; only then remove blobs, so a surviving reference keeps the
    # row and the blob together.
    current_keys = {f.key for f in await find_orphaned_files(db, user_id)}
    to_delete = [f for f in orphaned if f.key in current_keys]

    for f in to_delete:
        await db.delete(f)
    await db.commit()

    freed_bytes = 0
    for f in to_delete:
        await storage.delete(f.key)
        freed_bytes += f.size_bytes
        logger.info("Deleted orphaned file: %s (%d bytes)", f.key, f.size_bytes)

    return {
        "orphaned": len(to_delete),
        "freed_bytes": freed_bytes,
        "dry_run": False,
        "keys": [f.key for f in to_delete],
    }

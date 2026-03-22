"""Orphaned file cleanup and storage auditing.

Finds uploaded files that are no longer referenced by any avatar_url
or bio image, and deletes them. Adjusts storage_used_bytes accordingly.
Also provides storage usage auditing to fix counter drift.
"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.storage import get_storage

logger = logging.getLogger("sheaf.cleanup")

# Matches markdown image references: ![...](/v1/files/...)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((/v1/files/[^)]+)\)")

# All hosted file URLs start with this prefix
_FILE_PREFIX = "/v1/files/"


def _extract_keys_from_url(url: str | None) -> set[str]:
    """Extract the storage key from a /v1/files/ URL."""
    if url and url.startswith(_FILE_PREFIX):
        return {url[len(_FILE_PREFIX):]}
    return set()


def _extract_keys_from_markdown(text: str | None) -> set[str]:
    """Extract all hosted image keys from markdown content."""
    if not text:
        return set()
    keys = set()
    for match in _MD_IMAGE_RE.finditer(text):
        url = match.group(1)
        keys.update(_extract_keys_from_url(url))
    return keys


async def find_orphaned_files(
    db: AsyncSession,
    user_id: str,
) -> list[str]:
    """Find files uploaded by a user that are no longer referenced.

    Returns a list of orphaned storage keys.
    """
    storage = get_storage()
    prefix = f"avatars/{user_id}/"
    stored_keys = set(await storage.list_keys(prefix))

    if not stored_keys:
        return []

    # Collect all referenced keys for this user
    referenced: set[str] = set()

    # System avatar
    result = await db.execute(
        select(System.avatar_url).join(User).where(User.id == user_id)
    )
    for (avatar_url,) in result:
        referenced.update(_extract_keys_from_url(avatar_url))

    # Member avatars and bios
    result = await db.execute(
        select(Member.avatar_url, Member.description)
        .join(System)
        .join(User)
        .where(User.id == user_id)
    )
    for avatar_url, description in result:
        referenced.update(_extract_keys_from_url(avatar_url))
        referenced.update(_extract_keys_from_markdown(description))

    orphaned = stored_keys - referenced
    return sorted(orphaned)


async def cleanup_orphaned_files(
    db: AsyncSession,
    user_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Delete orphaned files for a user and adjust storage_used_bytes.

    Returns stats about what was (or would be) cleaned up.
    """
    orphaned = await find_orphaned_files(db, user_id)

    if not orphaned:
        return {"orphaned": 0, "freed_bytes": 0, "dry_run": dry_run}

    storage = get_storage()
    freed_bytes = 0

    for key in orphaned:
        size = await storage.size(key)
        freed_bytes += size
        if not dry_run:
            await storage.delete(key)
            logger.info("Deleted orphaned file: %s (%d bytes)", key, size)

    if not dry_run and freed_bytes > 0:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            user.storage_used_bytes = max(0, user.storage_used_bytes - freed_bytes)

    return {
        "orphaned": len(orphaned),
        "freed_bytes": freed_bytes,
        "dry_run": dry_run,
        "keys": orphaned if dry_run else [],
    }


async def audit_storage_usage(db: AsyncSession, user_id: str) -> dict:
    """Recalculate a user's actual storage usage from disk/S3.

    Compares actual usage with the tracked counter and corrects drift.
    Returns old value, new value, and whether it was corrected.
    """
    storage = get_storage()
    prefix = f"avatars/{user_id}/"
    keys = await storage.list_keys(prefix)

    actual_bytes = 0
    for key in keys:
        actual_bytes += await storage.size(key)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return {"error": "user not found"}

    old_bytes = user.storage_used_bytes
    corrected = old_bytes != actual_bytes

    if corrected:
        logger.info(
            "Storage audit for %s: tracked=%d actual=%d (correcting)",
            user_id, old_bytes, actual_bytes,
        )
        user.storage_used_bytes = actual_bytes

    return {
        "user_id": user_id,
        "tracked_bytes": old_bytes,
        "actual_bytes": actual_bytes,
        "file_count": len(keys),
        "corrected": corrected,
    }


async def audit_all_storage(db: AsyncSession) -> dict:
    """Audit storage usage for all users. Returns summary stats."""
    result = await db.execute(select(User.id))
    user_ids = [str(uid) for (uid,) in result]

    total_corrected = 0
    for uid in user_ids:
        audit = await audit_storage_usage(db, uid)
        if audit.get("corrected"):
            total_corrected += 1

    return {
        "users_checked": len(user_ids),
        "users_corrected": total_corrected,
    }

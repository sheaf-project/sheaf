"""Orphaned file cleanup.

Finds uploaded files that are no longer referenced by any avatar_url
or bio image, and deletes them from both storage and the database.
"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User
from sheaf.storage import get_storage

logger = logging.getLogger("sheaf.cleanup")

# Matches markdown image references: ![...](/v1/files/...)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((/v1/files/[^)]+)\)")

# All hosted file URLs start with this prefix
_FILE_PREFIX = "/v1/files/"


def _key_from_avatar(value: str | None) -> set[str]:
    """Return the storage key from an avatar_url DB field.

    avatar_url stores the storage key directly (e.g. avatars/user_id/uuid.png).
    """
    if value:
        return {value}
    return set()


def _extract_keys_from_markdown(text: str | None) -> set[str]:
    """Extract all hosted image keys from markdown image references."""
    if not text:
        return set()
    keys = set()
    for match in _MD_IMAGE_RE.finditer(text):
        url = match.group(1)
        # Markdown stores /v1/files/<key>?token=...&expires=... URLs
        # Strip prefix and query params to get the bare storage key
        if url.startswith(_FILE_PREFIX):
            key = url[len(_FILE_PREFIX):]
            if "?" in key:
                key = key.split("?", 1)[0]
            keys.add(key)
    return keys


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
        referenced.update(_extract_keys_from_markdown(description))

    orphaned_keys = uploaded_keys - referenced
    return [f for f in uploaded if f.key in orphaned_keys]


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
    freed_bytes = sum(f.size_bytes for f in orphaned)

    if not dry_run:
        for f in orphaned:
            await storage.delete(f.key)
            await db.delete(f)
            logger.info("Deleted orphaned file: %s (%d bytes)", f.key, f.size_bytes)

    return {
        "orphaned": len(orphaned),
        "freed_bytes": freed_bytes,
        "dry_run": dry_run,
        "keys": [f.key for f in orphaned],
    }

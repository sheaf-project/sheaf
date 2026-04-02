"""Dev-only scheduled jobs. Registered conditionally — see sheaf/services/jobs.py.

These jobs are destructive and intended ONLY for development/demo instances.
The sheaf_dev package is NOT installed in production Docker images, so these
jobs cannot exist in production even if misconfigured.
"""

import logging
import os

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("sheaf.dev_jobs")


async def wipe_demo_instance(db: AsyncSession) -> dict:
    """Delete all non-admin users and their data. For dev/demo instances only.

    This is the nuclear option — it removes every user account except admins,
    along with all their systems, members, fronts, files, sessions, etc.
    Designed for public dev instances that need periodic resets.
    """
    from sheaf.auth.sessions import delete_all_user_sessions
    from sheaf.models.uploaded_file import UploadedFile
    from sheaf.models.user import User
    from sheaf.storage import get_storage

    result = await db.execute(
        select(User).where(User.is_admin == False)  # noqa: E712
    )
    users = list(result.scalars().all())

    if not users:
        return {"items_processed": 0, "details": "No non-admin users to delete"}

    storage = get_storage()
    deleted = 0

    for user in users:
        # Delete storage files
        file_result = await db.execute(
            select(UploadedFile).where(UploadedFile.user_id == user.id)
        )
        for f in file_result.scalars().all():
            try:
                await storage.delete(f.key)
            except Exception:
                logger.warning("Failed to delete file %s for user %s", f.key, user.id)

        # Delete Redis sessions
        try:
            await delete_all_user_sessions(user.id)
        except Exception:
            logger.warning("Failed to delete sessions for user %s", user.id)

        # Delete user (CASCADE handles the rest)
        await db.execute(delete(User).where(User.id == user.id))
        deleted += 1

    logger.warning("DEV WIPE: deleted %d non-admin user(s)", deleted)

    return {
        "items_processed": deleted,
        "details": f"Wiped {deleted} non-admin user(s) from dev instance",
    }


def register_dev_jobs() -> None:
    """Register all dev-only jobs into the main job registry.

    Called from sheaf/services/jobs.py only when sheaf_dev is installed
    AND SHEAF_MODE is not 'production' (extra safety).
    """
    from sheaf.config import settings
    from sheaf.services.jobs import register_job

    # Double-check: never register in production even if somehow imported
    if settings.sheaf_mode.value == "saas":
        logger.warning("sheaf_dev is installed but SHEAF_MODE=saas — skipping dev job registration")
        return

    register_job(
        name="wipe_demo_instance",
        description="[DEV] Delete all non-admin users and their data",
        func=wipe_demo_instance,
        # Default: every 24 hours. Override with DEMO_WIPE_INTERVAL_HOURS env var.
        interval_seconds=lambda: int(os.environ.get("DEMO_WIPE_INTERVAL_HOURS", "24")) * 3600,
        # Only runs when explicitly enabled via env var
        enabled=lambda: os.environ.get("DEMO_WIPE_ENABLED", "false").lower() == "true",
    )

    logger.info("Dev jobs registered (%d jobs)", 1)


async def ensure_dev_announcement(db: AsyncSession) -> None:
    """Create a non-dismissible dev-mode warning banner if one doesn't exist.

    Called once at startup from the lifespan hook when sheaf_dev is installed.
    """
    from sheaf.models.announcement import ServerAnnouncement

    # Check if we already have a dev-mode announcement
    result = await db.execute(
        select(ServerAnnouncement).where(
            ServerAnnouncement.title == "Development Instance",
        )
    )
    if result.scalar_one_or_none() is not None:
        return

    announcement = ServerAnnouncement(
        title="Development Instance",
        body=(
            "This is a development/demo instance. Data may be wiped at any time."
            " Do not store anything important here."
        ),
        severity="warning",
        dismissible=False,
        active=True,
    )
    db.add(announcement)
    await db.commit()
    logger.info("Created dev-mode announcement banner")

"""Lightweight scheduled job system.

Jobs are registered at import time and run by a single async loop
in the FastAPI lifespan. Job history is stored in the job_runs table
for admin visibility. No external task queue needed.
"""

import asyncio
import logging
import traceback
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import SheafMode, settings
from sheaf.models.job_run import JobRun

logger = logging.getLogger("sheaf.jobs")

# Type alias for job functions
JobFunc = Callable[[AsyncSession], Coroutine[Any, Any, dict]]


@dataclass
class JobDefinition:
    name: str
    description: str
    func: JobFunc
    interval_seconds: Callable[[], int]
    enabled: Callable[[], bool]


_REGISTRY: dict[str, JobDefinition] = {}


def register_job(
    name: str,
    description: str,
    func: JobFunc,
    interval_seconds: Callable[[], int],
    enabled: Callable[[], bool] | None = None,
) -> None:
    """Register a job for periodic execution."""
    _REGISTRY[name] = JobDefinition(
        name=name,
        description=description,
        func=func,
        interval_seconds=interval_seconds,
        enabled=enabled or (lambda: True),
    )


def get_registry() -> dict[str, JobDefinition]:
    return _REGISTRY


async def run_job(job_name: str, db: AsyncSession) -> JobRun:
    """Execute a job and record the result. Returns the JobRun record."""
    job = _REGISTRY.get(job_name)
    if job is None:
        raise ValueError(f"Unknown job: {job_name}")

    run = JobRun(
        id=uuid.uuid4(),
        job_name=job_name,
        started_at=datetime.now(UTC),
        status="running",
        items_processed=0,
    )
    db.add(run)
    await db.flush()

    try:
        result = await job.func(db)
        run.status = "success"
        run.items_processed = result.get("items_processed", 0)
        run.details = result.get("details")
        run.finished_at = datetime.now(UTC)
    except Exception:
        run.status = "error"
        run.error_message = traceback.format_exc()
        run.finished_at = datetime.now(UTC)
        logger.exception("Job %s failed", job_name)

    await db.flush()
    return run


async def _get_last_success(job_name: str, db: AsyncSession) -> datetime | None:
    """Get the most recent successful run time for a job."""
    result = await db.execute(
        select(JobRun.started_at)
        .where(JobRun.job_name == job_name, JobRun.status == "success")
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row


async def job_runner_loop() -> None:
    """Main job runner loop. Runs in the FastAPI lifespan as an asyncio task."""
    from sheaf.database import async_session_factory

    # Ensure all jobs are registered
    _register_all_jobs()

    interval = settings.job_check_interval_minutes * 60
    logger.info(
        "Job runner started — checking every %dm, %d jobs registered",
        settings.job_check_interval_minutes,
        len(_REGISTRY),
    )

    while True:
        await asyncio.sleep(interval)

        for name, job in _REGISTRY.items():
            if not job.enabled():
                continue

            try:
                async with async_session_factory() as db:
                    last_success = await _get_last_success(name, db)

                    # Run if never run before, or if enough time has elapsed
                    if last_success is not None:
                        elapsed = (datetime.now(UTC) - last_success).total_seconds()
                        if elapsed < job.interval_seconds():
                            continue

                    logger.info("Running job: %s", name)
                    run = await run_job(name, db)
                    await db.commit()

                    if run.status == "success" and run.items_processed > 0:
                        logger.info(
                            "Job %s completed: %d items processed",
                            name, run.items_processed,
                        )
            except Exception:
                logger.exception("Job runner error for %s", name)


# ---------------------------------------------------------------------------
# Job implementations
# ---------------------------------------------------------------------------


async def _process_account_deletions(db: AsyncSession) -> dict:
    """Delete accounts past their grace period."""
    from sheaf.auth.sessions import delete_all_user_sessions
    from sheaf.models.uploaded_file import UploadedFile
    from sheaf.models.user import AccountStatus, User
    from sheaf.storage import get_storage

    grace = timedelta(days=settings.account_deletion_grace_days)
    cutoff = datetime.now(UTC) - grace

    result = await db.execute(
        select(User).where(
            User.account_status == AccountStatus.PENDING_DELETION,
            User.deletion_requested_at <= cutoff,
        )
    )
    users = list(result.scalars().all())

    if not users:
        return {"items_processed": 0}

    storage = get_storage()
    deleted = 0
    detail_lines: list[str] = []

    for user in users:
        # Delete storage files before cascade removes the DB rows
        file_result = await db.execute(
            select(UploadedFile).where(UploadedFile.user_id == user.id)
        )
        files = list(file_result.scalars().all())
        file_keys = []
        for f in files:
            try:
                await storage.delete(f.key)
                file_keys.append(f.key)
            except Exception:
                logger.warning("Failed to delete file %s for user %s", f.key, user.id)

        # Delete Redis sessions
        try:
            await delete_all_user_sessions(user.id)
        except Exception:
            logger.warning("Failed to delete sessions for user %s", user.id)

        # Delete user (DB CASCADE handles system, members, etc.)
        await db.execute(delete(User).where(User.id == user.id))
        deleted += 1
        logger.info("Permanently deleted account %s", user.id)

        line = f"Deleted user {user.id}"
        if file_keys:
            line += f" ({len(file_keys)} files: {', '.join(file_keys)})"
        detail_lines.append(line)

    return {
        "items_processed": deleted,
        "details": "\n".join(detail_lines) if detail_lines else None,
    }


async def _send_deletion_reminders(db: AsyncSession) -> dict:
    """Send reminder emails to users with pending deletions."""
    from sheaf.crypto import decrypt
    from sheaf.models.user import AccountStatus, User
    from sheaf.services.email import send_email
    from sheaf.services.email_templates import deletion_reminder_email

    reminder_days = [
        int(d.strip())
        for d in settings.account_deletion_reminder_days.split(",")
        if d.strip()
    ]

    if not reminder_days:
        return {"items_processed": 0}

    result = await db.execute(
        select(User).where(
            User.account_status == AccountStatus.PENDING_DELETION,
            User.deletion_requested_at.is_not(None),
        )
    )
    users = list(result.scalars().all())

    sent = 0
    detail_lines: list[str] = []
    for user in users:
        if user.deletion_requested_at is None:
            continue

        deletion_date = user.deletion_requested_at + timedelta(
            days=settings.account_deletion_grace_days
        )
        days_remaining = (deletion_date - datetime.now(UTC)).days

        already_sent = set()
        if user.deletion_reminders_sent:
            already_sent = {
                int(d.strip())
                for d in user.deletion_reminders_sent.split(",")
                if d.strip()
            }

        user_reminders: list[int] = []
        for reminder_day in reminder_days:
            if reminder_day in already_sent:
                continue
            if days_remaining <= reminder_day:
                try:
                    email = decrypt(user.email)
                    subject, html, text = deletion_reminder_email(
                        max(days_remaining, 0)
                    )
                    await send_email(email, subject, html, text)
                    already_sent.add(reminder_day)
                    user_reminders.append(reminder_day)
                    sent += 1
                except Exception:
                    logger.exception(
                        "Failed to send deletion reminder to user %s", user.id
                    )

        user.deletion_reminders_sent = ",".join(str(d) for d in sorted(already_sent))

        if user_reminders:
            detail_lines.append(
                f"User {user.id}: sent {len(user_reminders)}d reminder"
                f" ({days_remaining} days remaining)"
            )

    return {
        "items_processed": sent,
        "details": "\n".join(detail_lines) if detail_lines else None,
    }


async def _cleanup_unverified_accounts(db: AsyncSession) -> dict:
    """Delete accounts that never verified their email."""
    from sheaf.auth.sessions import delete_all_user_sessions
    from sheaf.models.uploaded_file import UploadedFile
    from sheaf.models.user import AccountStatus, User
    from sheaf.storage import get_storage

    cutoff = datetime.now(UTC) - timedelta(days=settings.unverified_account_cleanup_days)

    result = await db.execute(
        select(User).where(
            User.email_verified == False,  # noqa: E712
            User.created_at <= cutoff,
            User.account_status != AccountStatus.PENDING_DELETION,
            User.is_admin == False,  # noqa: E712
        )
    )
    users = list(result.scalars().all())

    if not users:
        return {"items_processed": 0}

    storage = get_storage()
    deleted = 0
    detail_lines: list[str] = []

    for user in users:
        file_result = await db.execute(
            select(UploadedFile).where(UploadedFile.user_id == user.id)
        )
        files = list(file_result.scalars().all())
        file_keys = []
        for f in files:
            try:
                await storage.delete(f.key)
                file_keys.append(f.key)
            except Exception:
                logger.warning("Failed to delete file %s for user %s", f.key, user.id)

        try:
            await delete_all_user_sessions(user.id)
        except Exception:
            logger.warning("Failed to delete sessions for user %s", user.id)

        await db.execute(delete(User).where(User.id == user.id))
        deleted += 1
        logger.info("Deleted unverified account %s (created %s)", user.id, user.created_at)

        line = f"Deleted unverified user {user.id} (created {user.created_at.isoformat()})"
        if file_keys:
            line += f" ({len(file_keys)} files: {', '.join(file_keys)})"
        detail_lines.append(line)

    return {
        "items_processed": deleted,
        "details": "\n".join(detail_lines) if detail_lines else None,
    }


async def _cleanup_orphaned_files(db: AsyncSession) -> dict:
    """Run orphaned file cleanup for all users."""
    from sheaf.models.user import User
    from sheaf.services.file_cleanup import cleanup_orphaned_files

    result = await db.execute(select(User.id))
    user_ids = [row[0] for row in result.all()]

    total_orphaned = 0
    total_freed = 0
    detail_lines: list[str] = []

    for user_id in user_ids:
        stats = await cleanup_orphaned_files(db, str(user_id))
        total_orphaned += stats["orphaned"]
        total_freed += stats["freed_bytes"]
        if stats["orphaned"] > 0:
            keys = stats.get("keys", [])
            detail_lines.append(
                f"User {user_id}: deleted {', '.join(keys)} "
                f"({stats['freed_bytes']} bytes)"
            )

    if total_freed > 0:
        detail_lines.insert(0, f"Total: {total_orphaned} files, {total_freed} bytes freed")

    return {
        "items_processed": total_orphaned,
        "details": "\n".join(detail_lines) if detail_lines else None,
    }


async def _prune_free_tier_fronts(db: AsyncSession) -> dict:
    """Wrapper around existing front retention pruning."""
    from sheaf.services.front_retention import prune_free_tier_fronts

    return await prune_free_tier_fronts(db)


# ---------------------------------------------------------------------------
# Job log cleanup
# ---------------------------------------------------------------------------


async def _cleanup_job_logs(db: AsyncSession) -> dict:
    """Delete job run logs older than the configured retention period."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.job_log_retention_days)
    result = await db.execute(
        delete(JobRun).where(JobRun.started_at < cutoff)
    )
    return {"items_processed": result.rowcount}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_registered = False


def _register_all_jobs() -> None:
    """Register all built-in jobs. Idempotent."""
    global _registered
    if _registered:
        return
    _registered = True

    register_job(
        name="process_account_deletions",
        description="Permanently delete accounts past their grace period",
        func=_process_account_deletions,
        interval_seconds=lambda: settings.job_check_interval_minutes * 60,
    )

    register_job(
        name="send_deletion_reminders",
        description="Send reminder emails before account deletion",
        func=_send_deletion_reminders,
        interval_seconds=lambda: settings.job_check_interval_minutes * 60,
        enabled=lambda: settings.email_backend != "none",
    )

    register_job(
        name="cleanup_unverified_accounts",
        description="Delete accounts that never verified their email",
        func=_cleanup_unverified_accounts,
        interval_seconds=lambda: settings.job_check_interval_minutes * 60,
        enabled=lambda: (
            settings.sheaf_mode == SheafMode.SAAS
            and settings.email_verification == "required"
        ),
    )

    register_job(
        name="cleanup_orphaned_files",
        description="Delete uploaded files no longer referenced by any member or system",
        func=_cleanup_orphaned_files,
        interval_seconds=lambda: settings.orphan_cleanup_interval_hours * 3600,
    )

    register_job(
        name="prune_free_tier_fronts",
        description="Prune front history older than retention window for free-tier users",
        func=_prune_free_tier_fronts,
        interval_seconds=lambda: settings.retention_check_interval_hours * 3600,
        enabled=lambda: settings.sheaf_mode == SheafMode.SAAS,
    )

    register_job(
        name="cleanup_job_logs",
        description="Delete job run logs older than 30 days",
        func=_cleanup_job_logs,
        interval_seconds=lambda: 86400,  # daily
    )

    # Dev-only jobs — sheaf_dev is NOT installed in production Docker images
    try:
        from sheaf_dev.jobs import register_dev_jobs

        register_dev_jobs()
    except ImportError:
        pass  # sheaf_dev not installed — this is expected in production

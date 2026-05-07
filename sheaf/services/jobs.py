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

            interval = job.interval_seconds()
            if interval <= 0:
                # Treat non-positive intervals as "disabled" — prevents a
                # misconfigured 0 from running the job every tick.
                continue

            try:
                async with async_session_factory() as db:
                    last_success = await _get_last_success(name, db)

                    # Run if never run before, or if enough time has elapsed
                    if last_success is not None:
                        elapsed = (datetime.now(UTC) - last_success).total_seconds()
                        if elapsed < interval:
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


async def _gc_revisions(db: AsyncSession) -> dict:
    """Wrapper around the revision-history retention sweep."""
    from sheaf.services.retention import gc_revisions

    return await gc_revisions(db)


# ---------------------------------------------------------------------------
# SES event processing (bounces + complaints)
# ---------------------------------------------------------------------------


async def _handle_ses_message(db: AsyncSession, raw_body: str) -> int:
    """Parse a single SQS message body (SES event) and apply state changes.

    Returns the number of user rows updated. Handles both raw SNS delivery
    (bare SES event JSON) and envelope-wrapped delivery.

    Uses shared email_events module for the actual state transitions.
    """
    import json

    from sheaf.services.email_events import apply_bounce, apply_complaint

    body = json.loads(raw_body)
    if isinstance(body, dict) and body.get("Type") == "Notification":
        body = json.loads(body["Message"])

    event_type = body.get("eventType") or body.get("notificationType")
    mail = body.get("mail") or {}
    message_id = mail.get("messageId", "?")

    updated = 0
    if event_type == "Bounce":
        bounce = body.get("bounce", {})
        bounce_type = bounce.get("bounceType", "Undetermined")
        for r in bounce.get("bouncedRecipients", []):
            addr = r.get("emailAddress")
            if addr and await apply_bounce(db, addr, permanent=bounce_type == "Permanent"):
                updated += 1
        logger.info("Processed SES Bounce (%s) messageId=%s", bounce_type, message_id)

    elif event_type == "Complaint":
        complaint = body.get("complaint", {})
        for r in complaint.get("complainedRecipients", []):
            addr = r.get("emailAddress")
            if addr and await apply_complaint(db, addr):
                updated += 1
        logger.info("Processed SES Complaint messageId=%s", message_id)

    else:
        logger.info("Ignoring SES event type: %s", event_type)

    return updated


async def _process_ses_events(db: AsyncSession) -> dict:
    """Drain the SES events SQS queue and apply bounce/complaint transitions."""
    if not settings.ses_events_queue_url:
        return {"items_processed": 0}

    try:
        import boto3
    except ImportError:
        logger.error(
            "process_ses_events: boto3 not installed — install sheaf[ses] "
            "or unset SHEAF_SES_EVENTS_QUEUE_URL"
        )
        return {"items_processed": 0}

    sqs = boto3.client(
        "sqs",
        region_name=settings.ses_region or "eu-west-1",
        aws_access_key_id=settings.ses_access_key or None,
        aws_secret_access_key=settings.ses_secret_key or None,
    )

    processed = 0
    max_iterations = 10  # drain up to ~100 messages per run

    for _ in range(max_iterations):
        resp = await asyncio.to_thread(
            sqs.receive_message,
            QueueUrl=settings.ses_events_queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=10,
            VisibilityTimeout=60,
        )
        messages = resp.get("Messages", [])
        if not messages:
            break

        to_delete: list[str] = []
        for msg in messages:
            try:
                await _handle_ses_message(db, msg["Body"])
            except Exception:
                logger.exception(
                    "Failed to handle SES event; leaving in queue for redelivery"
                )
                continue
            to_delete.append(msg["ReceiptHandle"])
            processed += 1

        # Commit DB changes before deleting SQS messages so a failure between
        # the two just causes a replay (bounce/complaint transitions are
        # idempotent for hard_bounced/complained; soft-bounce counter may
        # double-count on replay, which is acceptable).
        await db.commit()

        if to_delete:
            try:
                await asyncio.to_thread(
                    sqs.delete_message_batch,
                    QueueUrl=settings.ses_events_queue_url,
                    Entries=[
                        {"Id": str(i), "ReceiptHandle": h}
                        for i, h in enumerate(to_delete)
                    ],
                )
            except Exception:
                logger.exception("Failed to batch-delete SQS messages")

    return {"items_processed": processed}


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
# System Safety — finalize pending destructive actions + safety-setting changes
# ---------------------------------------------------------------------------


async def _finalize_pending_actions(db: AsyncSession) -> dict:
    """Execute pending destructive actions whose grace period has elapsed."""
    from sheaf.models.pending_action import PendingAction, PendingActionStatus
    from sheaf.services.system_safety import finalize_pending_action

    now = datetime.now(UTC)
    result = await db.execute(
        select(PendingAction).where(
            PendingAction.status == PendingActionStatus.PENDING,
            PendingAction.finalize_after <= now,
        )
    )
    pending = list(result.scalars().all())
    if not pending:
        return {"items_processed": 0}

    detail_lines: list[str] = []
    for row in pending:
        try:
            await finalize_pending_action(row, db)
            detail_lines.append(f"{row.action_type} {row.target_label}")
        except Exception as exc:
            row.status = PendingActionStatus.ERRORED
            row.error_message = str(exc)[:1000]
            row.completed_at = datetime.now(UTC)
            logger.exception("Failed to finalize pending action %s", row.id)

    return {
        "items_processed": len(pending),
        "details": "\n".join(detail_lines) if detail_lines else None,
    }


async def _finalize_safety_changes(db: AsyncSession) -> dict:
    """Apply deferred safety-setting loosenings whose grace period has elapsed."""
    from sheaf.models.safety_change_request import (
        SafetyChangeRequest,
        SafetyChangeStatus,
    )
    from sheaf.services.system_safety import finalize_safety_change

    now = datetime.now(UTC)
    result = await db.execute(
        select(SafetyChangeRequest).where(
            SafetyChangeRequest.status == SafetyChangeStatus.PENDING,
            SafetyChangeRequest.finalize_after <= now,
        )
    )
    changes = list(result.scalars().all())
    if not changes:
        return {"items_processed": 0}

    for row in changes:
        await finalize_safety_change(row, db)

    return {"items_processed": len(changes)}


# ---------------------------------------------------------------------------
async def _build_export_jobs(db: AsyncSession) -> dict:
    """Pick up one pending export job per tick and assemble its zip.

    The export builder manages its own session because the build phase is
    long enough that we don't want to hold a DB connection idle while
    streaming image bytes through. The db param here is unused; kept for
    job-runner signature consistency.
    """
    from sheaf.services.export_builder import run_build_tick

    del db
    handled = await run_build_tick()
    return {"items_processed": handled}


async def _cleanup_export_jobs(db: AsyncSession) -> dict:
    """Sweep expired DONE jobs: delete files, mark rows EXPIRED."""
    from sheaf.services.export_builder import run_cleanup_tick

    del db
    handled = await run_cleanup_tick()
    return {"items_processed": handled}


async def _tick_repeated_reminders(db: AsyncSession) -> dict:
    """Fire repeated reminders whose schedule has elapsed since last tick."""
    from sheaf.services.reminders import tick_repeated_reminders

    enqueued = await tick_repeated_reminders(db)
    await db.commit()
    return {"items_processed": enqueued}


async def _purge_expired_polls(db: AsyncSession) -> dict:
    """Delete polls whose retention window has elapsed."""
    from sheaf.services.polls import purge_expired_polls

    purged = await purge_expired_polls(db)
    await db.commit()
    return {"items_processed": purged}


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
        name="gc_revisions",
        description="Trim journal/bio revision history to per-user effective caps",
        func=_gc_revisions,
        interval_seconds=lambda: settings.journal_gc_interval_hours * 3600,
    )

    register_job(
        name="cleanup_job_logs",
        description="Delete job run logs older than 30 days",
        func=_cleanup_job_logs,
        interval_seconds=lambda: 86400,  # daily
    )

    register_job(
        name="finalize_pending_actions",
        description="Execute System Safety pending destructive actions past their grace period",
        func=_finalize_pending_actions,
        interval_seconds=lambda: settings.job_check_interval_minutes * 60,
    )

    register_job(
        name="finalize_safety_changes",
        description="Apply deferred System Safety setting loosenings past their grace period",
        func=_finalize_safety_changes,
        interval_seconds=lambda: settings.job_check_interval_minutes * 60,
    )

    register_job(
        name="process_ses_events",
        description="Process SES bounce/complaint events from the SQS queue",
        func=_process_ses_events,
        interval_seconds=lambda: settings.job_check_interval_minutes * 60,
        enabled=lambda: bool(settings.ses_events_queue_url),
    )

    register_job(
        name="build_export_jobs",
        description="Assemble pending data-export zips and persist them",
        func=_build_export_jobs,
        interval_seconds=lambda: settings.export_build_interval_seconds,
    )

    register_job(
        name="cleanup_export_jobs",
        description="Delete expired export artefacts and mark rows EXPIRED",
        func=_cleanup_export_jobs,
        interval_seconds=lambda: settings.export_cleanup_interval_seconds,
    )

    register_job(
        name="tick_repeated_reminders",
        description="Fire any due repeated reminders into the notification outbox",
        func=_tick_repeated_reminders,
        interval_seconds=lambda: 60,
    )

    register_job(
        name="purge_expired_polls",
        description="Delete polls past their retention window post-close",
        func=_purge_expired_polls,
        interval_seconds=lambda: settings.poll_cleanup_interval_hours * 3600,
    )

    # Dev-only jobs — sheaf_dev is NOT installed in production Docker images
    try:
        from sheaf_dev.jobs import register_dev_jobs

        register_dev_jobs()
    except ImportError:
        pass  # sheaf_dev not installed — this is expected in production

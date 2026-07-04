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
from sheaf.observability.metrics import (
    job_consecutive_failures,
    job_items_processed_total,
    job_last_success_timestamp,
    job_run_duration_seconds,
    job_runs_total,
)

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
    # True for jobs that delete or hard-mutate user data. These are the jobs
    # the destructive_jobs_enabled master switch pauses in one move.
    destructive: bool = False


_REGISTRY: dict[str, JobDefinition] = {}


def register_job(
    name: str,
    description: str,
    func: JobFunc,
    interval_seconds: Callable[[], int],
    enabled: Callable[[], bool] | None = None,
    destructive: bool = False,
) -> None:
    """Register a job for periodic execution."""
    _REGISTRY[name] = JobDefinition(
        name=name,
        description=description,
        func=func,
        interval_seconds=interval_seconds,
        enabled=enabled or (lambda: True),
        destructive=destructive,
    )


def get_registry() -> dict[str, JobDefinition]:
    return _REGISTRY


def _destructive_paused(job: JobDefinition) -> bool:
    """True when a destructive job must be skipped because the master kill
    switch is off. Split out so the runner loop and its tests share one
    definition of the freeze."""
    return job.destructive and not settings.destructive_jobs_enabled


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

    # Metrics: count outcome, observe duration, update items_processed,
    # set last-success timestamp, and track consecutive failures so
    # alerts can fire on "job has failed 5 times in a row".
    elapsed = (run.finished_at - run.started_at).total_seconds()
    job_runs_total.labels(job=job_name, outcome=run.status).inc()
    job_run_duration_seconds.labels(job=job_name).observe(elapsed)
    if run.status == "success":
        if run.items_processed:
            job_items_processed_total.labels(job=job_name).inc(run.items_processed)
        job_last_success_timestamp.labels(job=job_name).set(
            run.finished_at.timestamp()
        )
        _consecutive_failures[job_name] = 0
        job_consecutive_failures.labels(job=job_name).set(0)
    else:
        # Counter would be wrong here — failures aren't monotonic, they
        # reset on success. Use a private state dict so we can increment
        # against the current value without re-querying the registry.
        prev = _consecutive_failures.get(job_name, 0)
        _consecutive_failures[job_name] = prev + 1
        job_consecutive_failures.labels(job=job_name).set(prev + 1)

    return run


# Process-local consecutive-failure counter. Multiproc-safe-enough for v1:
# the gauge is set with multiprocess_mode="max" so the highest worker view
# wins, which matches the "have any of my workers seen 5 in a row?" intent.
_consecutive_failures: dict[str, int] = {}


async def _get_last_run_started(job_name: str, db: AsyncSession) -> datetime | None:
    """Most recent run start for a job, regardless of outcome.

    Scheduling keys off the last *attempt*, not the last success: with
    the wake cadence now following the fastest registered interval, a
    permanently-failing job scheduled off last-success would re-fire on
    every wake (potentially every 60s) instead of at its declared
    interval. Failures still surface through metrics and the admin job
    log; they don't earn a faster retry.
    """
    result = await db.execute(
        select(JobRun.started_at)
        .where(JobRun.job_name == job_name)
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row


def _compute_wake_seconds() -> int:
    """How long the runner sleeps between registry passes.

    The old fixed sleep of `job_check_interval_minutes` (15m default)
    silently floored every job's cadence: tick_repeated_reminders
    declares 60s but fired up to 14 minutes late, and queued export
    builds cleared one per 15 minutes. The runner now wakes as often as
    the fastest enabled job wants, clamped to [15s, the configured
    check interval]. Per-job elapsed checks still decide what actually
    runs, so slow jobs aren't affected by the faster wake.
    """
    ceiling = max(settings.job_check_interval_minutes * 60, 15)
    intervals = [
        job.interval_seconds()
        for job in _REGISTRY.values()
        if job.enabled() and job.interval_seconds() > 0
    ]
    if not intervals:
        return ceiling
    return max(15, min(min(intervals), ceiling))


async def job_runner_loop() -> None:
    """Main job runner loop. Runs in the FastAPI lifespan as an asyncio task."""
    from sheaf.database import async_session_factory

    # Ensure all jobs are registered
    _register_all_jobs()

    logger.info(
        "Job runner started — waking every %ds, %d jobs registered",
        _compute_wake_seconds(),
        len(_REGISTRY),
    )

    while True:
        await asyncio.sleep(_compute_wake_seconds())

        for name, job in _REGISTRY.items():
            if not job.enabled():
                continue

            # Incident-response freeze: a destructive job is skipped while the
            # master kill switch is off, exactly like a disabled one. One INFO
            # line per skip so an operator can see the freeze is in effect.
            if _destructive_paused(job):
                logger.info(
                    "skipping destructive job %s: destructive_jobs_enabled is off",
                    name,
                )
                continue

            # Use a job-scoped name - assigning to the outer `interval`
            # here would corrupt the loop's own wake cadence on the next
            # `asyncio.sleep(interval)`, drifting it to whatever the
            # last-registered job's interval happens to be.
            job_interval = job.interval_seconds()
            if job_interval <= 0:
                # Treat non-positive intervals as "disabled" — prevents a
                # misconfigured 0 from running the job every tick.
                continue

            try:
                async with async_session_factory() as db:
                    last_started = await _get_last_run_started(name, db)

                    # Run if never run before, or if enough time has elapsed
                    if last_started is not None:
                        elapsed = (datetime.now(UTC) - last_started).total_seconds()
                        if elapsed < job_interval:
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
        select(User)
        .where(
            User.account_status == AccountStatus.PENDING_DELETION,
            User.deletion_requested_at <= cutoff,
        )
        .with_for_update(skip_locked=True)
    )
    users = list(result.scalars().all())

    if not users:
        return {"items_processed": 0}

    storage = get_storage()
    deleted = 0
    detail_lines: list[str] = []

    for user in users:
        # Delete storage files before cascade removes the DB rows.
        file_result = await db.execute(
            select(UploadedFile).where(UploadedFile.user_id == user.id)
        )
        files = list(file_result.scalars().all())
        file_keys = []
        all_blobs_deleted = True
        for f in files:
            try:
                await storage.delete(f.key)
                file_keys.append(f.key)
            except Exception:
                all_blobs_deleted = False
                logger.warning("Failed to delete file %s for user %s", f.key, user.id)

        # Don't drop the user row while blobs survive: the CASCADE would
        # erase the UploadedFile records too, orphaning the storage objects
        # with nothing left to locate them by. Leave the account
        # PENDING_DELETION and retry on the next sweep.
        if not all_blobs_deleted:
            logger.warning(
                "Deferring deletion of account %s: storage cleanup incomplete",
                user.id,
            )
            continue

        # Delete Redis sessions
        try:
            await delete_all_user_sessions(user.id)
        except Exception:
            logger.warning("Failed to delete sessions for user %s", user.id)

        # Drop the rate-limit hit history too rather than letting it
        # ride out its TTL - erasure shouldn't leave a 48h echo. Best
        # effort for the same reason sessions are: the TTL is the
        # backstop if Redis blips here.
        try:
            from sheaf.middleware.rate_limit import delete_user_hit_history

            await delete_user_hit_history(user.id)
        except Exception:
            logger.warning(
                "Failed to delete rate-limit history for user %s", user.id
            )

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
        select(User)
        .where(
            User.account_status == AccountStatus.PENDING_DELETION,
            User.deletion_requested_at.is_not(None),
        )
        .with_for_update(skip_locked=True)
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
                    await send_email(email, subject, html, text, kind="deletion_reminder")
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


async def _unsuspend_expired(db: AsyncSession) -> dict:
    """Lift soft-bans whose `suspended_until` has passed.

    The auth dep already treats past-expiry suspends as effectively
    ACTIVE, so users aren't wedged in the gap before this fires; the
    sweep is the canonical state-cleaner. Writes a USER_UNSUSPEND
    audit row per restored account with admin_user_id NULL so the
    auto-restore is distinguishable from a manual unsuspend.
    """
    from sheaf.services.suspend import sweep_expired_suspensions

    restored = await sweep_expired_suspensions(db)
    await db.commit()
    return {"items_processed": restored}


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


async def _cleanup_security_events(db: AsyncSession) -> dict:
    """Delete security-event rows past the retention window.

    Bounded retention is the whole point: IP is personal data, so the
    log is a short investigation window, not a permanent archive.
    """
    from sheaf.models.security_event import SecurityEvent

    cutoff = datetime.now(UTC) - timedelta(
        days=settings.security_event_retention_days
    )
    result = await db.execute(
        delete(SecurityEvent).where(SecurityEvent.created_at < cutoff)
    )
    await db.commit()
    return {"items_processed": result.rowcount or 0}


async def _cleanup_activity_events(db: AsyncSession) -> dict:
    """Delete account-activity rows past the retention window so the log
    stays bounded. Generous window (no IP, it is the user's own record)."""
    from sheaf.models.activity_event import ActivityEvent

    cutoff = datetime.now(UTC) - timedelta(
        days=settings.activity_event_retention_days
    )
    result = await db.execute(
        delete(ActivityEvent).where(ActivityEvent.created_at < cutoff)
    )
    await db.commit()
    return {"items_processed": result.rowcount or 0}


# ---------------------------------------------------------------------------
# System Safety — finalize pending destructive actions + safety-setting changes
# ---------------------------------------------------------------------------


async def _finalize_pending_actions(db: AsyncSession) -> dict:
    """Execute pending destructive actions whose grace period has elapsed."""
    from sheaf.models.pending_action import PendingAction, PendingActionStatus
    from sheaf.services.system_safety import finalize_pending_action

    now = datetime.now(UTC)
    result = await db.execute(
        select(PendingAction)
        .where(
            PendingAction.status == PendingActionStatus.PENDING,
            PendingAction.finalize_after <= now,
        )
        .with_for_update(skip_locked=True)
    )
    pending = list(result.scalars().all())
    if not pending:
        return {"items_processed": 0}

    from sheaf.observability.metrics import pending_actions_finalized_total

    detail_lines: list[str] = []
    for row in pending:
        try:
            await finalize_pending_action(row, db)
            # Log the (non-sensitive) target id, not target_label: the label is
            # encrypted at rest, and job_runs.details is itself a retained
            # unencrypted column, so logging the decrypted label there would
            # reopen the same content leak this change closes.
            detail_lines.append(f"{row.action_type} {row.target_id}")
            pending_actions_finalized_total.labels(
                category=row.action_type, outcome="completed",
            ).inc()
        except Exception as exc:
            row.status = PendingActionStatus.ERRORED
            row.error_message = str(exc)[:1000]
            row.completed_at = datetime.now(UTC)
            logger.exception("Failed to finalize pending action %s", row.id)
            pending_actions_finalized_total.labels(
                category=row.action_type, outcome="errored",
            ).inc()

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


async def _cleanup_notification_outbox(db: AsyncSession) -> dict:
    """Drop terminal notification_outbox rows past the retention window.

    Every dispatched notification leaves a row behind, and so do the dropped
    ones - the dispatcher stamps `delivered_at` as the universal sentinel
    for "this row is done" (whether actually delivered, filtered out by the
    resolver, revoked, or permanently failed). Without this sweep the outbox
    grows unbounded; a busy system or a load test can leave thousands of
    rows. Only terminal rows (`delivered_at IS NOT NULL`) are eligible;
    anything still awaiting dispatch or in retry backoff is left alone.
    """
    from sheaf.models.notification_outbox import NotificationOutboxRow

    cutoff = datetime.now(UTC) - timedelta(
        days=settings.notification_outbox_retention_days
    )
    result = await db.execute(
        delete(NotificationOutboxRow).where(
            NotificationOutboxRow.delivered_at.is_not(None),
            NotificationOutboxRow.delivered_at < cutoff,
        )
    )
    await db.commit()
    return {"items_processed": result.rowcount or 0}


async def _cleanup_import_jobs(db: AsyncSession) -> dict:
    """Drop ImportJob rows past the retention window.

    Matches the cleanup_job_logs pattern (30 days). The user-facing
    /imports/{id} report is the value here — keeping it around long
    enough to be useful when someone says 'why is my system weird,
    let me check what that import did three weeks ago' — but the row
    isn't immortal.

    The uploaded payload blob was already deleted at finalize time, so
    this is purely DB row cleanup. CASCADE will sweep nothing because
    nothing references import_jobs.
    """
    from sheaf.models.import_job import ImportJob, ImportJobStatus

    cutoff = datetime.now(UTC) - timedelta(days=settings.import_job_retention_days)
    result = await db.execute(
        delete(ImportJob).where(
            ImportJob.finished_at.is_not(None),
            ImportJob.finished_at < cutoff,
            ImportJob.status.in_(
                [
                    ImportJobStatus.COMPLETE.value,
                    ImportJobStatus.FAILED.value,
                    ImportJobStatus.CANCELLED.value,
                ]
            ),
        )
    )
    await db.commit()
    return {"items_processed": result.rowcount or 0}


# A job that has crashed the worker this many times is parked as
# failed rather than reset again — otherwise a payload that
# reliably kills the runner would loop forever.
_IMPORT_MAX_ATTEMPTS = 3


async def _recover_stale_imports(db: AsyncSession) -> dict:
    """Reset ImportJob rows stuck in `running` after a worker crash.

    A worker killed mid-import leaves the row at status=running (the
    claim committed) but the import data never committed (the killed
    transaction rolled back) — so resetting to `pending` is a clean
    retry, no risk of double-import.

    failed_attempts is bumped on each reset; once it would exceed
    _IMPORT_MAX_ATTEMPTS the job is parked as `failed` instead, so a
    payload that reliably crashes the runner doesn't cycle forever.
    """
    from sheaf.models.import_job import ImportJob, ImportJobStatus
    from sheaf.services.import_runner import append_event

    cutoff = datetime.now(UTC) - timedelta(
        minutes=settings.import_stale_running_minutes
    )
    result = await db.execute(
        select(ImportJob).where(
            ImportJob.status == ImportJobStatus.RUNNING.value,
            ImportJob.claimed_at.is_not(None),
            ImportJob.claimed_at < cutoff,
        )
    )
    stale = list(result.scalars().all())
    if not stale:
        return {"items_processed": 0}

    reset = 0
    parked = 0
    for job in stale:
        job.failed_attempts += 1
        if job.failed_attempts >= _IMPORT_MAX_ATTEMPTS:
            job.status = ImportJobStatus.FAILED.value
            job.finished_at = datetime.now(UTC)
            job.last_error = (
                f"import worker did not finish; gave up after "
                f"{job.failed_attempts} stalled attempts"
            )
            append_event(
                job,
                level="error",
                stage="runner",
                message=(
                    "import abandoned — the worker stalled on this job "
                    f"{job.failed_attempts} times"
                ),
            )
            parked += 1
        else:
            job.status = ImportJobStatus.PENDING.value
            job.claimed_at = None
            job.claimed_by = None
            reset += 1
        logger.warning(
            "recovered stale import_job %s (attempt %d) -> %s",
            job.id,
            job.failed_attempts,
            job.status,
        )
    await db.commit()
    return {
        "items_processed": len(stale),
        "details": f"{reset} reset to pending, {parked} parked failed",
    }


# A build that has crashed the worker this many times is parked as
# failed rather than reset again, mirroring the import runner's cap.
_EXPORT_MAX_ATTEMPTS = 3


async def _recover_stale_exports(db: AsyncSession) -> dict:
    """Reset ExportJob rows stuck in RUNNING after a crash or deploy.

    The builder claims PENDING -> RUNNING and commits before the long
    build; a worker killed mid-build leaves the row RUNNING forever.
    Because create_export_job refuses new jobs while one is PENDING or
    RUNNING, the affected user was permanently wedged behind a 409 that
    only manual SQL could clear - every deploy that landed mid-build
    wedged someone.

    The claim committed but the build's writes didn't, so resetting to
    PENDING is a clean retry. failed_attempts caps the retries so a
    poisoned export can't crash-loop the worker forever.
    """
    from sheaf.models.export_job import ExportJob, ExportJobStatus

    cutoff = datetime.now(UTC) - timedelta(
        minutes=settings.export_stale_running_minutes
    )
    result = await db.execute(
        select(ExportJob)
        .where(
            ExportJob.status == ExportJobStatus.RUNNING,
            ExportJob.started_at.is_not(None),
            ExportJob.started_at < cutoff,
        )
        .with_for_update(skip_locked=True)
    )
    stale = list(result.scalars().all())
    if not stale:
        return {"items_processed": 0}

    reset = 0
    parked = 0
    for job in stale:
        job.failed_attempts += 1
        if job.failed_attempts >= _EXPORT_MAX_ATTEMPTS:
            job.status = ExportJobStatus.FAILED
            job.completed_at = datetime.now(UTC)
            job.error = (
                "export build did not finish; gave up after "
                f"{job.failed_attempts} stalled attempts"
            )
            parked += 1
        else:
            job.status = ExportJobStatus.PENDING
            job.started_at = None
            reset += 1
        logger.warning(
            "recovered stale export_job %s (attempt %d) -> %s",
            job.id,
            job.failed_attempts,
            job.status,
        )
    await db.commit()
    return {
        "items_processed": len(stale),
        "details": f"{reset} reset to pending, {parked} parked failed",
    }


# Registration
# ---------------------------------------------------------------------------

_registered = False


def _register_all_jobs() -> None:
    """Register all built-in jobs. Idempotent."""
    global _registered
    if _registered:
        return
    _registered = True

    # process_account_deletions and finalize_pending_actions execute
    # USER-REQUESTED deletions (a scheduled account deletion past its grace;
    # System Safety destructive actions past their grace). They are included in
    # the freeze so an incident pause stops ALL data deletion, in-flight user
    # requests included. If an operator instead wants user-requested deletions
    # to keep flowing while destructive_jobs_enabled is off, flip these two to
    # destructive=False - that is the one deliberate change to make here.
    register_job(
        name="process_account_deletions",
        description="Permanently delete accounts past their grace period",
        func=_process_account_deletions,
        interval_seconds=lambda: settings.job_check_interval_minutes * 60,
        destructive=True,
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
        destructive=True,
    )

    register_job(
        name="cleanup_orphaned_files",
        description="Delete uploaded files no longer referenced by any member or system",
        func=_cleanup_orphaned_files,
        interval_seconds=lambda: settings.orphan_cleanup_interval_hours * 3600,
        destructive=True,
    )

    register_job(
        name="prune_free_tier_fronts",
        description="Prune front history older than retention window for free-tier users",
        func=_prune_free_tier_fronts,
        interval_seconds=lambda: settings.retention_check_interval_hours * 3600,
        enabled=lambda: settings.sheaf_mode == SheafMode.SAAS,
        destructive=True,
    )

    register_job(
        name="gc_revisions",
        description="Trim journal/bio revision history to per-user effective caps",
        func=_gc_revisions,
        interval_seconds=lambda: settings.journal_gc_interval_hours * 3600,
        destructive=True,
    )

    register_job(
        name="unsuspend_expired",
        description="Restore soft-banned accounts whose suspension window has elapsed",
        func=_unsuspend_expired,
        interval_seconds=lambda: settings.job_check_interval_minutes * 60,
    )

    register_job(
        name="cleanup_job_logs",
        description="Delete job run logs older than 30 days",
        func=_cleanup_job_logs,
        interval_seconds=lambda: 86400,  # daily
    )

    # NOT marked destructive on purpose. This deletes IP-bearing security
    # events to honour the retention promise: it removes no user content, it
    # enforces a privacy obligation. It carries its own switch
    # (security_event_cleanup_enabled) so the destructive_jobs_enabled master
    # pause cannot silently stop IP minimisation while it is engaged.
    register_job(
        name="cleanup_security_events",
        description="Delete security-event rows past the retention window",
        func=_cleanup_security_events,
        interval_seconds=lambda: 86400,  # daily
        enabled=lambda: settings.security_event_cleanup_enabled,
    )

    register_job(
        name="cleanup_activity_events",
        description="Delete account-activity rows past the retention window",
        func=_cleanup_activity_events,
        interval_seconds=lambda: 86400,  # daily
        destructive=True,
    )

    # See the note on process_account_deletions above: this finalizes
    # user-requested System Safety deletions and is frozen with them. Flip to
    # destructive=False if user-requested deletions should keep running during
    # an incident pause.
    register_job(
        name="finalize_pending_actions",
        description="Execute System Safety pending destructive actions past their grace period",
        func=_finalize_pending_actions,
        interval_seconds=lambda: settings.job_check_interval_minutes * 60,
        destructive=True,
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
        destructive=True,
    )

    # NOTE: the import *runner* is NOT registered here. It needs a
    # few-second tick, but this registry only wakes every
    # job_check_interval_minutes — far too slow for an import a user is
    # waiting on. It runs as its own loop (import_runner_loop) in the
    # FastAPI lifespan, same pattern as the notification dispatcher.
    # Only the slow daily cleanup of old ImportJob rows belongs here.
    register_job(
        name="cleanup_import_jobs",
        description="Delete ImportJob rows past their retention window",
        func=_cleanup_import_jobs,
        interval_seconds=lambda: 86400,  # daily
    )

    register_job(
        name="cleanup_notification_outbox",
        description="Delete terminal notification_outbox rows past retention",
        func=_cleanup_notification_outbox,
        interval_seconds=lambda: 86400,  # daily
    )

    register_job(
        name="recover_stale_exports",
        description="Reset ExportJob rows stuck running after a worker crash",
        func=_recover_stale_exports,
        interval_seconds=lambda: settings.export_stale_running_minutes * 60,
    )

    register_job(
        name="recover_stale_imports",
        description="Reset ImportJob rows stuck running after a worker crash",
        func=_recover_stale_imports,
        interval_seconds=lambda: settings.import_stale_running_minutes * 60,
    )

    # Background refresher for DB- and Redis-sourced metrics gauges.
    # Cheap; the queries are all COUNT(*) on indexed predicates plus
    # bounded Redis SCANs. Disabled when metrics are off so it doesn't
    # waste a tick on every deployment.
    from sheaf.observability.gauges import (
        refresh_gauge_distributions as _refresh_metrics_gauge_distributions,
    )
    from sheaf.observability.gauges import refresh_gauges as _refresh_metrics_gauges

    register_job(
        name="refresh_metrics_gauges",
        description="Refresh DB- and Redis-sourced Prometheus gauges",
        func=_refresh_metrics_gauges,
        interval_seconds=lambda: settings.metrics_gauge_refresh_seconds,
        enabled=lambda: settings.metrics_enabled,
    )

    # Heavy per-system / per-target distribution gauges. Whole-table scans
    # aggregated in SQL to a single row each; they change slowly and feed
    # capacity/retention decisions, not alerting, so they run hourly rather
    # than on the 60s gauge cadence. Runs once on startup (no prior run) so
    # the distribution gauges populate promptly.
    register_job(
        name="refresh_metrics_gauge_distributions",
        description="Refresh per-system / per-target distribution gauges",
        func=_refresh_metrics_gauge_distributions,
        interval_seconds=lambda: 3600,  # hourly
        enabled=lambda: settings.metrics_enabled,
    )

    # Dev-only jobs — sheaf_dev is NOT installed in production Docker images
    try:
        from sheaf_dev.jobs import register_dev_jobs

        register_dev_jobs()
    except ImportError:
        pass  # sheaf_dev not installed — this is expected in production

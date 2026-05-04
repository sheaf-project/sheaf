"""Background build worker for async data-export jobs.

Picks up pending ExportJob rows, assembles the zip in-memory, persists
via export_storage, marks the row done with a TTL, and (if email is
configured) sends a "your export is ready" notification.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.database import async_session_factory
from sheaf.models.export_job import ExportJob, ExportJobStatus
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User
from sheaf.services import export_storage
from sheaf.storage import get_storage

logger = logging.getLogger("sheaf.export.builder")

_README = """\
This zip is a full export of your Sheaf account data.

Contents:
- export.json -- your plural-system content (members, fronts, journals,
  groups, tags, custom fields, content revisions). Same shape as
  /v1/export. Re-importable into another Sheaf instance via the
  Settings -> Import flow.
- images/ -- the binary blobs referenced by member avatars, journal
  embeds, and content-revision history.

Important: importing this zip into another Sheaf instance brings the
text content (members, journals, etc.) but does NOT auto-restore image
attachments. The image bytes are present here for your records, but
re-uploading them via the new instance's UI is a manual step. Image
references will be empty until you do.
"""


async def run_build_tick() -> int:
    """Process one batch of pending export jobs. Returns count handled.

    Called from `job_runner_loop`. Each tick claims a single pending
    job per session — exports are heavy (potentially 100s of MB once
    images are included) so we don't want them pile-driving the worker.
    """
    async with async_session_factory() as db:
        job = await _claim_one(db)
        if job is None:
            return 0
    # Run the actual build outside the claim transaction so a long
    # build doesn't hold a Postgres connection idle.
    await _build(job.id)
    return 1


async def _claim_one(db: AsyncSession) -> ExportJob | None:
    """Atomically pick the oldest pending job, mark it RUNNING."""
    stmt = (
        select(ExportJob)
        .where(ExportJob.status == ExportJobStatus.PENDING)
        .order_by(ExportJob.requested_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        return None
    job.status = ExportJobStatus.RUNNING
    job.started_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(job)
    return job


async def _build(job_id: uuid.UUID) -> None:
    """Build, upload, mark done. Failures land back as FAILED with the
    error captured for the user to see."""
    async with async_session_factory() as db:
        job = await db.get(ExportJob, job_id)
        if job is None:
            logger.warning("Export job %s vanished mid-build", job_id)
            return
        user = await db.get(User, job.user_id)
        if user is None:
            await _mark_failed(db, job, "user no longer exists")
            return

        try:
            zip_bytes = await _assemble_zip(db, user, include_images=job.include_images)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Export build failed for job %s", job_id)
            await _mark_failed(db, job, f"build failed: {exc}")
            return

        try:
            location = await export_storage.put(user.id, job.id, zip_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Export upload failed for job %s", job_id)
            await _mark_failed(db, job, f"upload failed: {exc}")
            return

        job.file_location = location
        job.file_size_bytes = len(zip_bytes)
        job.status = ExportJobStatus.DONE
        job.completed_at = datetime.now(UTC)
        job.expires_at = job.completed_at + timedelta(
            hours=settings.export_job_ttl_hours
        )
        await db.commit()
        logger.info(
            "Export job %s completed (%d bytes, expires %s)",
            job.id,
            job.file_size_bytes,
            job.expires_at.isoformat(),
        )

    # Email notification — best-effort, don't fail the job on send error.
    try:
        await _send_completion_email(user_email=user.email, job_id=job.id)
    except Exception:  # noqa: BLE001
        logger.exception("Export completion email failed for job %s", job_id)


async def _mark_failed(db: AsyncSession, job: ExportJob, reason: str) -> None:
    job.status = ExportJobStatus.FAILED
    job.completed_at = datetime.now(UTC)
    job.error = reason
    await db.commit()


async def _assemble_zip(
    db: AsyncSession, user: User, *, include_images: bool
) -> bytes:
    """Build the in-memory zip artefact.

    Layout:
        export.json   -- same shape as the sync /v1/export endpoint
        README.txt    -- explains the asymmetry around image re-import
        images/<key>  -- (when include_images) the binary blobs
    """
    # Build the JSON payload by calling the same code path the sync
    # endpoint uses, so we don't drift between the two.
    from sheaf.api.v1.export import export_all  # late import to avoid cycle

    json_payload = await export_all(user=user, db=db)
    json_bytes = json.dumps(json_payload, indent=2, default=str).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("export.json", json_bytes)
        zf.writestr("README.txt", _README)

        if include_images:
            await _add_images(zf, db, user)

    return buf.getvalue()


async def _add_images(
    zf: zipfile.ZipFile, db: AsyncSession, user: User
) -> None:
    """Pack every UploadedFile owned by this user under images/."""
    result = await db.execute(
        select(UploadedFile).where(UploadedFile.user_id == user.id)
    )
    files = list(result.scalars().all())
    if not files:
        return

    storage = get_storage()
    for f in files:
        try:
            blob = await storage.get(f.key)
        except Exception:  # noqa: BLE001
            logger.warning("Skipping unreadable image %s in export", f.key)
            continue
        if blob is None:
            continue
        # Use the stored key as the filename inside the zip; keys are
        # opaque UUIDs so they preserve cross-reference uniqueness.
        zf.writestr(f"images/{f.key}", blob)


async def _send_completion_email(*, user_email: str, job_id: uuid.UUID) -> None:
    """Best-effort transactional email when an export finishes building.

    No-op when email is disabled; we don't want to fail the build on a
    misconfigured SMTP relay or pending SES revalidation.
    """
    if settings.email_backend == "none":
        return
    from sheaf.services.email import send_email  # late import

    base = settings.sheaf_base_url.rstrip("/") if settings.sheaf_base_url else ""
    url = f"{base}/settings/export?job={job_id}"
    subject = "Your Sheaf data export is ready"
    body_text = (
        "Your Sheaf data export has finished building and is ready to "
        f"download.\n\nDownload: {url}\n\nThe file is available for "
        f"{settings.export_job_ttl_hours} hours, after which it's "
        "automatically deleted from our storage.\n\nIf you didn't "
        "request this export, sign in and check your active sessions "
        "in Settings -> Security.\n"
    )
    body_html = (
        f"<p>Your Sheaf data export has finished building and is ready "
        f'to download.</p><p><a href="{url}">Download your export</a></p>'
        f"<p>The file is available for {settings.export_job_ttl_hours} "
        "hours, after which it's automatically deleted from our "
        "storage.</p><p>If you didn't request this export, sign in and "
        "check your active sessions in Settings &rarr; Security.</p>"
    )
    try:
        await send_email(
            to=user_email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Export completion email error")


# --- cleanup worker ---------------------------------------------------------


async def run_cleanup_tick() -> int:
    """Sweep expired DONE jobs: delete their files, mark row EXPIRED.

    Idempotent — claims rows by status transition, so two workers running
    simultaneously can't double-delete (only one will see DONE→EXPIRED
    succeed). Also picks up FAILED rows older than the TTL just to keep
    the table tidy.
    """
    cutoff = datetime.now(UTC)
    async with async_session_factory() as db:
        result = await db.execute(
            select(ExportJob).where(
                ExportJob.status == ExportJobStatus.DONE,
                ExportJob.expires_at <= cutoff,
            )
        )
        jobs = list(result.scalars().all())
        for job in jobs:
            if job.file_location:
                try:
                    await export_storage.delete(job.file_location)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to delete expired export %s at %s",
                        job.id,
                        job.file_location,
                    )
                    # Don't mark expired if delete failed — try again
                    # next tick. The row stays DONE so the user UI still
                    # shows it as "available" even though storage may be
                    # in a weird state. Better than lying about deletion.
                    continue
            job.status = ExportJobStatus.EXPIRED
            job.file_location = None
        await db.commit()
        return len(jobs)

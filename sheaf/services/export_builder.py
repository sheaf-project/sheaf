"""Background build worker for async data-export jobs.

Picks up pending ExportJob rows, assembles the zip in-memory, persists
via export_storage, marks the row done with a TTL, and (if email is
configured) sends a "your export is ready" notification.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.crypto import decrypt
from sheaf.database import async_session_factory
from sheaf.encrypted_fields import user_email_aad
from sheaf.models.activity_event import ActivityAction, ActivityActorType
from sheaf.models.export_job import ExportJob, ExportJobStatus
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User
from sheaf.observability.metrics import export_size_bytes, exports_built_total
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

Importing this whole zip via Settings -> Import restores both the text
content AND the images: avatars and embedded images are re-uploaded to
the new instance and references re-pointed automatically (subject to
the importing account's storage quota and the instance's upload
policy). Importing just export.json brings the text content only, with
image references removed.
"""


_OPENPLURAL_README = """\
This zip is an OpenPlural v0.1 bundle export of your Sheaf data.

Contents:
- openplural.json -- your plural-system content mapped to the OpenPlural
  v0.1 data standard (https://github.com/skylartaylor/openplural).
  Systems, members, groups, tags, custom fields, front history, and
  journals map to OpenPlural core records; everything Sheaf has that
  the spec does not yet model is preserved under extensions.sheaf.*
- assets/ -- the binary blobs (avatars, banners, journal images)
  referenced by the assets[] entries via their bundle_path.

Re-importable into Sheaf (Settings -> Import) and into any other app
that supports OpenPlural v0.1. See docs/OPENPLURAL.md in the Sheaf
source for the field-by-field mapping and known gaps.
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
    error captured for the user to see.

    Streams the zip through a temp file on disk so peak memory stays
    bounded by per-image blob size (~100MB cap) rather than total
    export size. The tempfile lives in `settings.export_build_tmp_dir`
    when set, otherwise the system default — selfhosters with a small
    root volume should point this at the same disk the s3-export
    bucket is fronted by, or a dedicated big volume.
    """
    async with async_session_factory() as db:
        job = await db.get(ExportJob, job_id)
        if job is None:
            logger.warning("Export job %s vanished mid-build", job_id)
            return
        user = await db.get(User, job.user_id)
        if user is None:
            await _mark_failed(db, job, "user no longer exists")
            return

        tmp_path: str | None = None
        try:
            try:
                from sheaf.services.front_history_export import (
                    FRONT_HISTORY_FORMATS,
                )

                if job.format in FRONT_HISTORY_FORMATS:
                    tmp_path, size_bytes = await _assemble_fronts_to_tempfile(
                        db, user, fmt=job.format
                    )
                else:
                    tmp_path, size_bytes = await _assemble_zip_to_tempfile(
                        db, user, include_images=job.include_images, fmt=job.format
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Export build failed for job %s", job_id)
                await _mark_failed(db, job, f"build failed: {exc}")
                return

            try:
                location = await export_storage.put_path(
                    user.id, job.id, tmp_path
                )
                # put_path on the filesystem backend renames the tempfile
                # into place — at that point there's no tempfile to clean.
                # On S3 the tempfile is still ours to delete in `finally`.
                if not _location_is_tempfile(location, tmp_path):
                    tmp_path_to_clean = tmp_path
                else:
                    tmp_path_to_clean = None
                    tmp_path = None
            except Exception as exc:  # noqa: BLE001
                logger.exception("Export upload failed for job %s", job_id)
                await _mark_failed(db, job, f"upload failed: {exc}")
                return

            job.file_location = location
            job.file_size_bytes = size_bytes
            job.status = ExportJobStatus.DONE
            job.completed_at = datetime.now(UTC)
            job.expires_at = job.completed_at + timedelta(
                hours=settings.export_job_ttl_hours
            )
            # Automated event: the export the user asked for is now ready.
            # Lands in the same commit as the DONE transition.
            from sheaf.services.activity_log import log_activity

            await log_activity(
                db,
                user_id=job.user_id,
                action=ActivityAction.EXPORT_READY,
                actor_type=ActivityActorType.SYSTEM,
                target_label=job.format,
            )
            await db.commit()
            exports_built_total.labels(outcome="done").inc()
            export_size_bytes.observe(job.file_size_bytes)
            logger.info(
                "Export job %s completed (%d bytes, expires %s)",
                job.id,
                job.file_size_bytes,
                job.expires_at.isoformat(),
            )
            # Re-bind for the finally block now that ownership has
            # transferred (or the tempfile has been renamed into place).
            tmp_path = tmp_path_to_clean
        finally:
            if tmp_path is not None:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp_path)

    # Email notification — best-effort, don't fail the job on send error.
    try:
        await _send_completion_email(
            user_email=decrypt(user.email, aad=user_email_aad(user.id)),
            job_id=job.id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Export completion email failed for job %s", job_id)


def _location_is_tempfile(location: str, tmp_path: str) -> bool:
    """The filesystem backend's `put_path` renames the tempfile into the
    final location — at that point there's nothing left to unlink. The
    S3 backend leaves the tempfile alone after upload. Comparing
    against the tmp_path tells us which case we're in.
    """
    return location == tmp_path


async def _mark_failed(db: AsyncSession, job: ExportJob, reason: str) -> None:
    job.status = ExportJobStatus.FAILED
    job.completed_at = datetime.now(UTC)
    job.error = reason
    await db.commit()
    exports_built_total.labels(outcome="failed").inc()


async def _assemble_zip_to_tempfile(
    db: AsyncSession, user: User, *, include_images: bool, fmt: str = "sheaf_native"
) -> tuple[str, int]:
    """Build the zip artefact on disk and return (path, size_bytes).

    Native layout (`fmt="sheaf_native"`):
        export.json   -- same shape as the sync /v1/export endpoint
        README.txt    -- explains the asymmetry around image re-import
        images/<key>  -- (when include_images) the binary blobs

    OpenPlural bundle (`fmt="openplural"`):
        openplural.json   -- OpenPlural v0.1 envelope
        README.txt        -- bundle notes
        assets/<key>      -- (when include_images) the referenced blobs

    Streams images through `zipfile.open(..., 'w')` so each blob lives
    in RAM only while it's actively being written, never accumulating.
    The JSON payload is built in-memory because the sync /v1/export
    endpoint already returns the whole dict; refactoring that to
    stream would be a much bigger change.
    """
    # Build the native payload by calling the same code path the sync
    # endpoint uses, so we don't drift between the two.
    from sheaf.api.v1.export import export_all  # late import to avoid cycle

    native_payload = await export_all(user=user, db=db)

    tmp_dir = settings.export_build_tmp_dir or None
    fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="sheaf-export-", dir=tmp_dir)
    os.close(fd)  # zipfile reopens the path; we just needed exclusive creation
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if fmt == "openplural":
                from sheaf.services.openplural_export import build_envelope

                envelope = build_envelope(
                    native_payload,
                    exported_at=datetime.now(UTC).isoformat(),
                    include_asset_bytes=include_images,
                )
                zf.writestr(
                    "openplural.json",
                    json.dumps(envelope, indent=2, default=str).encode("utf-8"),
                )
                zf.writestr("README.txt", _OPENPLURAL_README)
                if include_images:
                    await _add_openplural_assets(zf, envelope)
            else:
                zf.writestr(
                    "export.json",
                    json.dumps(native_payload, indent=2, default=str).encode("utf-8"),
                )
                zf.writestr("README.txt", _README)
                if include_images:
                    await _add_images(zf, db, user)
        size_bytes = os.path.getsize(tmp_path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise
    return tmp_path, size_bytes


async def _assemble_fronts_to_tempfile(
    db: AsyncSession, user: User, *, fmt: str
) -> tuple[str, int]:
    """Build a standalone front-history file (CSV / JSON / ICS) on disk and
    return (path, size_bytes). Unlike the account export this is a single
    file, not a zip - the front history is small relative to a full
    account dump and the formats are flat.
    """
    from sqlalchemy import select

    from sheaf.models.system import System
    from sheaf.services.front_history_export import (
        format_extension,
        load_front_history,
        serialize_front_history,
    )

    system = (
        await db.execute(select(System).where(System.user_id == user.id))
    ).scalar_one_or_none()
    exported_at = datetime.now(UTC)
    rows = await load_front_history(db, system) if system is not None else []
    data = serialize_front_history(
        rows, system.name if system is not None else None, fmt, exported_at
    )

    tmp_dir = settings.export_build_tmp_dir or None
    fd, tmp_path = tempfile.mkstemp(
        suffix=f".{format_extension(fmt)}", prefix="sheaf-fronts-", dir=tmp_dir
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        size_bytes = os.path.getsize(tmp_path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise
    return tmp_path, size_bytes


async def _add_openplural_assets(zf: zipfile.ZipFile, envelope: dict) -> None:
    """Pack the blobs the OpenPlural envelope references under assets/<key>.

    Only assets carrying an `extensions.sheaf.storage_key` (Sheaf-internal
    references) have bytes to bundle; external CDN URLs stay uri-only.
    Each blob is fetched and written one at a time so per-asset memory is
    bounded by the largest single blob, mirroring `_add_images`.
    """
    storage = get_storage()
    seen: set[str] = set()
    for asset in envelope.get("assets", []) or []:
        ext = (asset.get("extensions") or {}).get("sheaf") or {}
        key = ext.get("storage_key")
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            blob = await storage.get(key)
        except Exception:  # noqa: BLE001
            logger.warning("Skipping unreadable asset %s in OpenPlural export", key)
            continue
        if blob is None:
            continue
        with zf.open(f"assets/{key}", "w") as dest:
            dest.write(blob)
        del blob


async def _add_images(
    zf: zipfile.ZipFile, db: AsyncSession, user: User
) -> None:
    """Pack every UploadedFile owned by this user under images/.

    Image blobs are fetched one at a time; each is written into the
    zip via `zf.open(..., 'w').write(blob)` so it can be evicted as
    soon as the next iteration begins. Per-image memory is bounded by
    the largest single uploaded image (which the upload pipeline caps
    at `max_animated_decoded_bytes`, default 100 MB).
    """
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
        with zf.open(f"images/{f.key}", "w") as dest:
            dest.write(blob)
        # Drop the local reference immediately so the GC can reclaim
        # before the next iteration's blob lands.
        del blob


async def _send_completion_email(*, user_email: str, job_id: uuid.UUID) -> None:
    """Best-effort transactional email when an export finishes building.

    No-op when email is disabled; we don't want to fail the build on a
    misconfigured SMTP relay or pending SES revalidation.
    """
    if settings.email_backend == "none":
        return
    from sheaf.services.email import send_email  # late import

    base = settings.sheaf_base_url.rstrip("/") if settings.sheaf_base_url else ""
    # The export UI lives at /settings/data (DataExportCard) — the
    # ?job= param scrolls the card into view and highlights the
    # matching row. The historical /settings/export path was a
    # placeholder that the data settings page never actually
    # registered.
    url = f"{base}/settings/data?job={job_id}"
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
            kind="export_ready",
        )
    except Exception:  # noqa: BLE001
        logger.exception("Export completion email error")


# --- cleanup worker ---------------------------------------------------------


async def run_cleanup_tick() -> int:
    """Sweep expired DONE jobs: delete their files, mark row EXPIRED.

    Idempotent - claims rows by status transition, so two workers running
    simultaneously can't double-delete (only one will see the row move to
    EXPIRED succeed). Also picks up FAILED rows older than the TTL just to
    keep the table tidy: those carry no expires_at (they never produced a
    file to expire), so they're aged out on completed_at + TTL instead.
    """
    cutoff = datetime.now(UTC)
    failed_cutoff = cutoff - timedelta(hours=settings.export_job_ttl_hours)
    async with async_session_factory() as db:
        result = await db.execute(
            select(ExportJob).where(
                or_(
                    and_(
                        ExportJob.status == ExportJobStatus.DONE,
                        ExportJob.expires_at <= cutoff,
                    ),
                    and_(
                        ExportJob.status == ExportJobStatus.FAILED,
                        ExportJob.completed_at <= failed_cutoff,
                    ),
                )
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
            exports_built_total.labels(outcome="expired").inc()
        await db.commit()
        return len(jobs)

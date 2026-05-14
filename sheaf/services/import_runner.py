"""Async import job runner.

Polled by the existing jobs.py dispatcher every few seconds. On each
tick, claims one pending ImportJob with FOR UPDATE SKIP LOCKED, looks
up the registered handler for that job's source, and runs it. The
handler is responsible for the actual import work; this module is just
the lifecycle + bookkeeping wrapper.

Handlers are registered at module import time via `register_handler`.
Each handler takes the claimed ImportJob plus its own db session and
mutates job.counts / job.events as it goes. Hard failures raise; soft
per-record errors land in events with level=error.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from sheaf.models.import_job import ImportJob, ImportJobStatus
from sheaf.services.import_storage import delete_payload

logger = logging.getLogger("sheaf.imports")

# Soft cap on the per-job events array. Past this, info + warning
# events stop being appended (after a single "_truncated" marker) so
# the JSONB row can't grow unboundedly on a pathological import.
# Errors always keep appending past this cap with full detail — losing
# diagnostic info on the actual failures defeats the point of having
# the report.
MAX_EVENTS_PER_JOB = 10_000

# Handler signature: takes the loaded job and an open db session.
# Mutates job.counts / job.events as it processes; raises on hard
# failure (which the runner catches and converts to status=failed).
ImportHandler = Callable[[ImportJob, AsyncSession], Awaitable[None]]

_HANDLERS: dict[str, ImportHandler] = {}


def register_handler(source: str, handler: ImportHandler) -> None:
    """Register a handler for a given ImportJobSource value. Idempotent;
    re-registering the same source replaces the previous handler."""
    _HANDLERS[source] = handler


def append_event(
    job: ImportJob,
    *,
    level: str,
    stage: str,
    message: str,
    record_ref: str | None = None,
    **extra: Any,
) -> None:
    """Append a structured event to job.events with bounded length.

    `level` is one of "info" | "warning" | "error". `stage` names the
    importer phase ("parse", "members", "switches", ...). `record_ref`
    is a source-specific identifier (HID, tupper id, member name) so
    the UI can point the user at the problem row.
    """
    if level not in ("info", "warning", "error"):
        raise ValueError(f"invalid event level: {level!r}")
    events = job.events or []
    over_cap = len(events) >= MAX_EVENTS_PER_JOB
    if over_cap and level != "error":
        # Info / warning past the cap: append the truncation marker
        # exactly once, then drop further low-priority events. Errors
        # still go through (handled below).
        if not events or events[-1].get("stage") != "_truncated":
            events.append(
                {
                    "level": "warning",
                    "stage": "_truncated",
                    "message": (
                        f"event log truncated at {MAX_EVENTS_PER_JOB} entries; "
                        "subsequent info / warning events dropped — "
                        "errors still recorded below"
                    ),
                }
            )
            job.events = events
            flag_modified(job, "events")
        return
    entry: dict[str, Any] = {"level": level, "stage": stage, "message": message}
    if record_ref is not None:
        entry["record_ref"] = record_ref
    if extra:
        entry.update(extra)
    # Errors past the cap still get appended (they appear after the
    # _truncated marker so the report has the full failure context the
    # user actually needs to debug).
    events.append(entry)
    job.events = events
    flag_modified(job, "events")


def update_counts(job: ImportJob, **deltas: int) -> None:
    """Increment named counters on job.counts. Missing keys start at 0.

    Mark the JSONB column dirty so SQLAlchemy flushes the in-place
    mutation; without flag_modified() the change is invisible at
    commit time.
    """
    counts = dict(job.counts or {})
    for key, delta in deltas.items():
        counts[key] = counts.get(key, 0) + delta
    job.counts = counts
    flag_modified(job, "counts")


def _worker_id() -> str:
    """Identifier for the current process used to populate claimed_by."""
    return f"{os.uname().nodename}:{os.getpid()}"


async def _claim_next_pending(db: AsyncSession) -> ImportJob | None:
    """Claim one pending ImportJob row using FOR UPDATE SKIP LOCKED.

    Multi-worker safe: two workers ticking at the same time will pick
    different rows (or one picks nothing) because SKIP LOCKED makes
    each transaction see only rows the other hasn't grabbed.
    """
    stmt = (
        select(ImportJob)
        .where(ImportJob.status == ImportJobStatus.PENDING.value)
        .order_by(ImportJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        return None
    now = datetime.now(UTC)
    job.status = ImportJobStatus.RUNNING.value
    job.started_at = now
    job.claimed_at = now
    job.claimed_by = _worker_id()
    await db.flush()
    await db.commit()
    return job


async def _finalize(
    job: ImportJob,
    db: AsyncSession,
    *,
    status: ImportJobStatus,
    last_error: str | None = None,
) -> None:
    """Mark a job done (or failed/cancelled), clear sensitive payload
    metadata, and best-effort delete the storage blob."""
    job.status = status.value
    job.finished_at = datetime.now(UTC)
    if last_error is not None:
        job.last_error = last_error[:8000]
    # Wipe credential-bearing metadata once the job is terminal so a
    # compromised DB dump after the fact doesn't leak the PK API token
    # the user passed at upload time. The storage blob is also deleted.
    if job.payload_metadata:
        # Keep non-secret metadata (options, selected ids) — only strip
        # the encrypted-credential blob if present.
        scrubbed = {
            k: v for k, v in job.payload_metadata.items() if k != "encrypted_credential"
        }
        job.payload_metadata = scrubbed or None
        flag_modified(job, "payload_metadata")
    storage_key = job.payload_storage_key
    job.payload_storage_key = None
    await db.commit()
    if storage_key:
        await delete_payload(storage_key)


async def run_import_tick(db: AsyncSession) -> dict:
    """One scheduler tick: claim a pending job (if any) and run it.

    Returns the jobs.py-style result dict so the existing job_runs
    log table picks up `items_processed` and `details`.
    """
    job = await _claim_next_pending(db)
    if job is None:
        return {"items_processed": 0}

    handler = _HANDLERS.get(job.source)
    if handler is None:
        # Unregistered source — this is a deployment / migration bug,
        # not a user error. Mark failed loudly so it shows up.
        append_event(
            job,
            level="error",
            stage="dispatch",
            message=f"no handler registered for source={job.source!r}",
        )
        await _finalize(
            job,
            db,
            status=ImportJobStatus.FAILED,
            last_error=f"no handler registered for source={job.source!r}",
        )
        logger.error("import_job %s has no handler for source=%s", job.id, job.source)
        return {"items_processed": 1, "details": f"no handler: {job.source}"}

    logger.info(
        "import_job %s starting (source=%s, user=%s)",
        job.id,
        job.source,
        job.user_id,
    )
    try:
        await handler(job, db)
    except Exception as exc:
        logger.exception("import_job %s failed", job.id)
        append_event(
            job,
            level="error",
            stage="runner",
            message=f"unhandled exception: {exc!s}"[:1000],
        )
        # Roll back any uncommitted importer state so the failure mode
        # is consistent: either we committed up to a savepoint the
        # importer chose, or nothing.
        await db.rollback()
        # Reload the (rolled-back) job row so we can write terminal state.
        job = await db.get(ImportJob, job.id)
        if job is None:
            return {"items_processed": 1, "details": "job vanished mid-run"}
        await _finalize(
            job, db, status=ImportJobStatus.FAILED, last_error=str(exc)[:8000]
        )
        return {"items_processed": 1, "details": f"failed: {job.id}"}

    await _finalize(job, db, status=ImportJobStatus.COMPLETE)
    logger.info(
        "import_job %s complete (counts=%s)", job.id, job.counts
    )
    return {"items_processed": 1, "details": f"complete: {job.id}"}


# Public re-export so the importer modules don't reach into the
# private name when registering themselves at module import time.
__all__ = [
    "ImportHandler",
    "MAX_EVENTS_PER_JOB",
    "append_event",
    "register_handler",
    "run_import_tick",
    "update_counts",
]


def _register_builtin_handlers() -> None:
    """Import the per-source handler modules so their module-level
    `register_handler(...)` calls land. Lazy import avoids a circular
    import between this module and the importer modules at startup.

    Each phase of the larger import migration adds one entry here.
    Phase 1 lands the runner with no handlers wired up — the runner
    no-ops on any pending row, marks it failed with 'no handler', and
    that's an explicit error the developer sees immediately.
    """
    # Phase 3: from sheaf.services import pk_import_runner  # noqa: F401
    # Phase 4: from sheaf.services import pk_api_runner  # noqa: F401
    # Phase 5: from sheaf.services import tb_import_runner  # noqa: F401
    # Phase 5: from sheaf.services import sp_import_runner  # noqa: F401
    # Phase 6: from sheaf.services import sheaf_import_runner  # noqa: F401
    pass


_register_builtin_handlers()

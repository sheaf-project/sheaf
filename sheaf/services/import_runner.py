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

import asyncio
import contextlib
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from sheaf.config import settings
from sheaf.models.activity_event import ActivityAction, ActivityActorType
from sheaf.models.import_job import ImportJob, ImportJobStatus
from sheaf.models.system import System
from sheaf.observability.metrics import (
    imports_completed_total,
    imports_started_total,
)
from sheaf.services.import_parsing import ImportPayloadError
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


async def load_user_system(db: AsyncSession, user_id: uuid.UUID) -> System:
    """Locate the importing user's system, or raise ImportPayloadError.

    Shared by every per-source handler — an import has to land somewhere,
    and "you haven't created a system yet" is a user-facing precondition
    failure, not a bug. Raising ImportPayloadError gets it surfaced as a
    clean failed-job message rather than an unhandled traceback.
    """
    result = await db.execute(select(System).where(System.user_id == user_id))
    system = result.scalar_one_or_none()
    if system is None:
        raise ImportPayloadError(
            "no system found on this account — create a system before importing"
        )
    return system


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
    imports_started_total.labels(source=job.source).inc()
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
    if status is ImportJobStatus.COMPLETE:
        # Automated event: the user's data changed without a request the
        # account-activity surface would otherwise record. Lands in the
        # same commit as the terminal job row.
        from sheaf.services.activity_log import log_activity

        await log_activity(
            db,
            user_id=job.user_id,
            action=ActivityAction.IMPORT_COMPLETED,
            actor_type=ActivityActorType.SYSTEM,
            target_label=job.source,
            detail=dict(job.counts) if job.counts else None,
        )
    await db.commit()
    if storage_key:
        await delete_payload(storage_key)
    # Map ImportJobStatus -> a small terminal-outcome label set so the
    # completed counter is alertable. Anything not in the map (shouldn't
    # happen — only the three terminal statuses pass through here) gets
    # "failed" to err on the side of visibility.
    outcome_label = {
        ImportJobStatus.COMPLETE: "complete",
        ImportJobStatus.FAILED: "failed",
        ImportJobStatus.CANCELLED: "cancelled",
    }.get(status, "failed")
    imports_completed_total.labels(
        source=job.source, outcome=outcome_label,
    ).inc()


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
    job_id = job.id
    try:
        await handler(job, db)
    except Exception as exc:
        # ImportPayloadError is a *classified, expected* failure — bad
        # payload, PK API unreachable, no system on the account. It is
        # not a bug. Log it as a clean one-line warning (the message
        # already names the cause) rather than a 60-line traceback, and
        # don't dress it up as an "unhandled exception" in the report.
        # Anything else is an actual bug: full traceback, loud.
        expected = isinstance(exc, ImportPayloadError)
        if expected:
            logger.warning("import_job %s failed: %s", job_id, exc)
        else:
            logger.exception("import_job %s failed", job_id)
        # Roll back the importer's uncommitted state, then write the
        # terminal failed record from a *fresh* session. The handler's
        # session is in an unknown state after the raise — reusing it
        # risks an expired-attribute sync lazy-load (MissingGreenlet).
        # All-or-nothing: a hard failure discards everything the
        # importer did, including any progress events; the terminal
        # error event names the stage so the report still says where
        # it broke.
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            logger.exception("rollback after import_job %s failure failed", job_id)
        from sheaf.database import async_session_factory

        async with async_session_factory() as fresh:
            fresh_job = await fresh.get(ImportJob, job_id)
            if fresh_job is None:
                return {"items_processed": 1, "details": "job vanished mid-run"}
            append_event(
                fresh_job,
                level="error",
                stage="runner",
                message=(
                    str(exc) if expected else f"unhandled exception: {exc!s}"
                )[:1000],
            )
            await _finalize(
                fresh_job,
                fresh,
                status=ImportJobStatus.FAILED,
                last_error=str(exc)[:8000],
            )
        return {"items_processed": 1, "details": f"failed: {job_id}"}

    await _finalize(job, db, status=ImportJobStatus.COMPLETE)
    logger.info(
        "import_job %s complete (counts=%s)", job.id, job.counts
    )
    return {"items_processed": 1, "details": f"complete: {job.id}"}


async def _listen_for_enqueues(wake: asyncio.Event) -> None:
    """Hold a LISTEN connection and set `wake` on every enqueue NOTIFY.

    The enqueue endpoint sends `NOTIFY sheaf_import_enqueued` inside the
    same transaction that inserts the job, so the runner reacts the
    moment the row is visible instead of waiting out its poll interval.
    This is an accelerator, not a correctness mechanism: the runner's
    timed poll stays as the safety net, so a dropped notification or a
    listener reconnect window can delay a job by at most one poll
    interval, never lose it.
    """
    from sqlalchemy import text

    from sheaf.database import engine

    while True:
        try:
            async with engine.connect() as conn:
                raw = await conn.get_raw_connection()
                driver = raw.driver_connection  # asyncpg connection
                await driver.add_listener(
                    "sheaf_import_enqueued", lambda *_: wake.set()
                )
                logger.info("Import runner listening for enqueue NOTIFYs")
                # No remove_listener teardown: the listener dies with the
                # connection when this context exits, and that is the only
                # way out of the inner loop.
                while True:
                    # Liveness probe: raises when the connection has died,
                    # dropping us to the reconnect path.
                    await asyncio.sleep(60)
                    await conn.execute(text("SELECT 1"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Import enqueue listener lost its connection; reconnecting",
                exc_info=True,
            )
            await asyncio.sleep(5)


async def import_runner_loop(stop_event: asyncio.Event | None = None) -> None:
    """Dedicated import-runner loop, run as its own asyncio task in the
    FastAPI lifespan — NOT registered in the jobs.py periodic registry.

    The jobs.py registry wakes far too slowly for an import the user is
    actively waiting on. This loop mirrors the notification dispatcher:
    its own session per tick — but instead of pure polling it sleeps on
    an event the LISTEN connection sets whenever an import is enqueued,
    with the poll interval as the timeout. Enqueued imports start within
    milliseconds; the poll remains the safety net for anything a NOTIFY
    could miss (listener reconnecting, jobs reset by the stale sweep).

    Each pass drains the queue — keep claiming until empty — so a
    backlog of N pending jobs clears in one pass rather than taking
    N * interval seconds. `run_import_tick` claims exactly one pending
    job and drives it to a terminal state, so the drain always
    terminates (a processed job is no longer pending).
    """
    from sheaf.database import async_session_factory

    interval = max(1, settings.import_runner_interval_seconds)
    wake = asyncio.Event()
    listener = asyncio.create_task(
        _listen_for_enqueues(wake), name="import-enqueue-listener"
    )
    logger.info("Import runner started (poll fallback=%ds)", interval)
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                while True:
                    async with async_session_factory() as db:
                        result = await run_import_tick(db)
                    if result.get("items_processed", 0) == 0:
                        break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Import runner tick failed")
            try:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), timeout=interval)
                wake.clear()
            except asyncio.CancelledError:
                raise
    finally:
        listener.cancel()
        with contextlib.suppress(Exception):
            await listener


# Public re-export so the importer modules don't reach into the
# private name when registering themselves at module import time.
__all__ = [
    "ImportHandler",
    "MAX_EVENTS_PER_JOB",
    "append_event",
    "import_runner_loop",
    "load_user_system",
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
    # pk_import_runner registers both pluralkit_file and pluralkit_api.
    from sheaf.services import (
        ampersand_import_runner,  # noqa: F401
        openplural_import_runner,  # noqa: F401
        pk_import_runner,  # noqa: F401
        pluralspace_import_runner,  # noqa: F401
        prism_import_runner,  # noqa: F401
        sheaf_archive_import_runner,  # noqa: F401
        sheaf_import_runner,  # noqa: F401
        sp_import_runner,  # noqa: F401
        tb_import_runner,  # noqa: F401
    )


_register_builtin_handlers()

"""Unified import endpoints.

All four importer flavours (PluralKit file, PluralKit API, Tupperbox,
SimplyPlural, Sheaf native re-import) enqueue through this router and
get processed asynchronously by the import job runner. The user then
polls `/v1/imports/{id}` to watch progress and read the per-record
report.

The legacy per-source endpoints (under `/v1/import/...`) are scheduled
for removal in a later phase; see migration notes in the design doc.
For now they exist side by side, with the legacy ones marked
deprecated and the recommended path going through this router.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.crypto import encrypt
from sheaf.database import get_db
from sheaf.models.import_job import ImportJob, ImportJobStatus
from sheaf.models.user import User
from sheaf.schemas.imports import (
    ImportApiCreateRequest,
    ImportFileCreateRequest,
    ImportJobList,
    ImportJobRead,
    ImportJobSummary,
)
from sheaf.services.import_storage import (
    delete_payload,
    make_payload_key,
    put_payload,
)

logger = logging.getLogger("sheaf.imports.api")

router = APIRouter(prefix="/imports", tags=["imports"])


# Hard cap on uploaded file size. The global MaxBodySizeMiddleware
# rejects oversize requests upstream of this handler, but enforcing
# here too gives a clearer per-endpoint error than the generic 413.
MAX_IMPORT_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB


# --- Helpers ----------------------------------------------------------------


async def _find_existing_idempotent(
    db: AsyncSession, *, user_id: uuid.UUID, idempotency_key: uuid.UUID
) -> ImportJob | None:
    """Look up an existing job by (user, idempotency_key). The unique
    constraint guarantees at most one match."""
    stmt = select(ImportJob).where(
        ImportJob.user_id == user_id,
        ImportJob.idempotency_key == str(idempotency_key),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _commit_new_job(
    db: AsyncSession,
    job: ImportJob,
    *,
    user_id: uuid.UUID,
    idempotency_key: uuid.UUID,
) -> ImportJob:
    """Commit a freshly-built ImportJob, resolving the idempotency race.

    The pre-insert _find_existing_idempotent check is not atomic with
    the INSERT: two concurrent requests carrying the same key can both
    miss it and both try to insert. The uq_import_jobs_user_idempotency
    constraint rejects the loser — turn that IntegrityError into the
    dedupe behaviour the constraint promises (return the row the winner
    created) instead of letting it surface as a 500.
    """
    db.add(job)
    try:
        # Wake the import runner the moment this lands instead of waiting
        # out its poll interval. NOTIFY is transactional: it fires on
        # commit and evaporates with a rollback, so the idempotency-race
        # loser below never pings anyone.
        await db.execute(text("NOTIFY sheaf_import_enqueued"))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _find_existing_idempotent(
            db, user_id=user_id, idempotency_key=idempotency_key
        )
        if existing is None:
            # Not the idempotency constraint — some other integrity
            # violation. Don't swallow it.
            raise
        return existing
    await db.refresh(job)
    return job


async def _load_owned_job(
    db: AsyncSession, *, user_id: uuid.UUID, job_id: uuid.UUID
) -> ImportJob:
    job = await db.get(ImportJob, job_id)
    if job is None or job.user_id != user_id:
        # Same 404 either way — don't leak the existence of someone
        # else's job ids.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Import job not found"
        )
    return job


# --- POST endpoints ---------------------------------------------------------


@router.post(
    "/file",
    response_model=ImportJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_file_import(
    file: Annotated[UploadFile, File()],
    source: Annotated[str, Form()],
    idempotency_key: Annotated[uuid.UUID, Form()],
    options: Annotated[str | None, Form()] = None,
    credential: Annotated[str | None, Form()] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImportJobRead:
    """Enqueue a file-based import. Returns 202 + the newly-created
    job row (or the existing one if `idempotency_key` matches).

    The file is read into memory once, sanity-checked for size, and
    handed to the storage backend under the imports/ prefix. The
    runner picks the job up on its next tick and walks the payload.

    `options` is a JSON-encoded form field — source-specific shape.

    `credential` is an optional per-source secret (currently only used
    by Prism's PRISM1 envelope passphrase). When set, it is encrypted
    at rest in `payload_metadata.encrypted_credential` using
    SHEAF_ENCRYPTION_KEY and wiped by the runner at terminal state,
    mirroring the PluralKit API token flow.
    """
    options_dict: dict | None = None
    if options is not None and options.strip():
        try:
            options_dict = json.loads(options)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"options field is not valid JSON: {exc}",
            ) from exc
        if not isinstance(options_dict, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="options must be a JSON object",
            )

    try:
        body = ImportFileCreateRequest(
            source=source,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,
            options=options_dict,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()
        ) from exc

    # Idempotency: if the same (user, key) has been seen, return the
    # existing job rather than creating a duplicate. This is the
    # double-click defence.
    existing = await _find_existing_idempotent(
        db, user_id=user.id, idempotency_key=body.idempotency_key
    )
    if existing is not None:
        return ImportJobRead.model_validate(existing)

    data = await file.read()
    if len(data) > MAX_IMPORT_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Import file too large ({len(data)} bytes). "
                f"Max {MAX_IMPORT_UPLOAD_BYTES} bytes."
            ),
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Import file is empty.",
        )

    job_id = uuid.uuid4()
    storage_key = make_payload_key(job_id, file.filename or "import")
    # Default content-type for our known importer formats is JSON. The
    # storage backend doesn't actually care — the type is metadata for
    # operators inspecting the bucket.
    await put_payload(storage_key, data, content_type="application/json")

    metadata: dict | None = None
    if body.options is not None or credential:
        metadata = {}
        if body.options is not None:
            metadata["options"] = body.options
        if credential:
            metadata["encrypted_credential"] = encrypt(credential)

    job = ImportJob(
        id=job_id,
        user_id=user.id,
        source=body.source.value,
        status=ImportJobStatus.PENDING.value,
        idempotency_key=str(body.idempotency_key),
        payload_storage_key=storage_key,
        payload_metadata=metadata,
        counts={},
        events=[],
    )
    job = await _commit_new_job(
        db, job, user_id=user.id, idempotency_key=body.idempotency_key
    )
    if job.id != job_id:
        # Lost the idempotency race — the winning job has its own
        # payload; the blob we just uploaded for this losing attempt is
        # now orphaned. Best-effort delete it rather than leave litter.
        await delete_payload(storage_key)
        return ImportJobRead.model_validate(job)
    logger.info(
        "import_job %s enqueued (source=%s, user=%s, size=%d)",
        job.id,
        job.source,
        user.id,
        len(data),
    )
    return ImportJobRead.model_validate(job)


@router.post(
    "/api",
    response_model=ImportJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_api_import(
    body: ImportApiCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImportJobRead:
    """Enqueue a credential-based import (currently PluralKit API).

    The token is encrypted at rest in payload_metadata while the job
    runs and gets wiped at finalize time, so a post-completion DB dump
    can't leak it.
    """
    existing = await _find_existing_idempotent(
        db, user_id=user.id, idempotency_key=body.idempotency_key
    )
    if existing is not None:
        return ImportJobRead.model_validate(existing)

    encrypted_token = encrypt(body.pk_token)

    # Explicit id so the post-commit race check can compare against it
    # (the UUIDMixin default is only applied at flush time).
    new_id = uuid.uuid4()
    job = ImportJob(
        id=new_id,
        user_id=user.id,
        source=body.source.value,
        status=ImportJobStatus.PENDING.value,
        idempotency_key=str(body.idempotency_key),
        payload_storage_key=None,
        payload_metadata={
            "encrypted_credential": encrypted_token,
            "options": body.options if body.options is not None else {},
        },
        counts={},
        events=[],
    )
    job = await _commit_new_job(
        db, job, user_id=user.id, idempotency_key=body.idempotency_key
    )
    if job.id != new_id:
        # Lost the idempotency race — return the winner. The losing
        # row (with its encrypted token) was rolled back, nothing to
        # clean up.
        return ImportJobRead.model_validate(job)
    logger.info(
        "import_job %s enqueued (source=%s, user=%s, credential-based)",
        job.id,
        job.source,
        user.id,
    )
    return ImportJobRead.model_validate(job)


# --- GET endpoints ----------------------------------------------------------


@router.get("", response_model=ImportJobList)
async def list_imports(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 25,
    include_archived: bool = False,
    cursor: str | None = None,
) -> ImportJobList:
    """List the current user's imports, most-recent first.

    Excludes archived rows by default — the UI shows them via a
    separate "show archived" toggle.

    Cursor pagination: when the response carries a `next_cursor`, pass
    it back as the `cursor` query param to fetch the next page. The
    cursor is the ISO timestamp of the last row on the current page;
    the next page is everything strictly older than it.
    """
    if not 1 <= limit <= 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="limit must be between 1 and 100",
        )
    stmt = (
        select(ImportJob)
        .where(ImportJob.user_id == user.id)
        .order_by(ImportJob.created_at.desc())
        .limit(limit + 1)
    )
    if not include_archived:
        stmt = stmt.where(ImportJob.archived_at.is_(None))
    if cursor is not None:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="cursor must be an ISO-8601 timestamp",
            ) from exc
        stmt = stmt.where(ImportJob.created_at < cursor_dt)
    rows = list((await db.execute(stmt)).scalars().all())
    next_cursor = None
    if len(rows) > limit:
        # Drop the trailing peek row and synthesize a continuation cursor
        # from the last-included item's created_at. created_at carries
        # enough precision (microseconds) that a strict `<` comparison
        # won't skip or repeat rows at realistic enqueue rates.
        rows = rows[:limit]
        next_cursor = rows[-1].created_at.isoformat()
    return ImportJobList(
        items=[ImportJobSummary.model_validate(r) for r in rows],
        next_cursor=next_cursor,
    )


@router.get("/{job_id}", response_model=ImportJobRead)
async def get_import(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImportJobRead:
    job = await _load_owned_job(db, user_id=user.id, job_id=job_id)
    return ImportJobRead.model_validate(job)


# --- DELETE ----------------------------------------------------------------


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_or_archive_import(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """For pending jobs: cancel before the runner picks them up.
    For terminal jobs (complete / failed / cancelled): archive — they
    drop out of the default history list. Running jobs can't be
    cancelled mid-flight in v1; user has to wait for the runner to
    finish or fail."""
    job = await _load_owned_job(db, user_id=user.id, job_id=job_id)

    if job.status == ImportJobStatus.PENDING.value:
        job.status = ImportJobStatus.CANCELLED.value
        job.finished_at = datetime.now(UTC)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if job.status == ImportJobStatus.RUNNING.value:
        # Mid-flight cancel needs a cooperative cancellation channel
        # that we don't have in v1. Refuse rather than do something
        # surprising — the runner will finish in seconds anyway.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Import is currently running and cannot be cancelled. "
                "Wait for it to finish, then archive."
            ),
        )

    # Terminal state: archive.
    if job.archived_at is None:
        job.archived_at = datetime.now(UTC)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

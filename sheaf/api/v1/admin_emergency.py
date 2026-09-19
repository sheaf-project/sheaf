"""Admin emergency-support endpoints.

Actions support operators can take against a user account, all gated
behind admin auth, all but the reads requiring a reason string, all
logged in the admin audit table:

  - GET /admin/users/{id}/pending: what is queued on the account, in
    the three shapes it comes in (deletions, safety-setting changes,
    staged exposures). Counts and timestamps only. Read it before
    pressing any of the levers below, which used to be pressed blind.

  - POST /admin/users/{id}/reset-safety: put System Safety back to how
    a new account starts and cancel any queued loosening that would
    undo that. Use when a user accidentally locks themselves out with
    strict safeguards. Deliberately does NOT touch already-queued
    pending_actions or staged exposures; see its own docstring for why
    each is left where it is.

  - POST /admin/users/{id}/bypass-pending: finalize every pending
    System Safety action on the user's system NOW, without waiting
    out the grace period. Use when a user has stuck deletions in the
    queue and wants them through right away. Reaches deletions only:
    every PendingActionType is a delete, an unpin or a revoke.

  - POST /admin/users/{id}/cancel-exposures: call off every staged
    flip-to-public raise, so nothing parked behind the visibility
    grace window goes live. The un-exposing counterpart to
    bypass-pending, which has no exposing counterpart on purpose:
    support may always stop a publication that has not happened, and
    may never bring one forward.

  - GET /admin/users/{id}/import-jobs and
    GET /admin/import-jobs/{job_id}: read the user's import-job
    events. The single-job detail is privacy-sensitive — importer
    events MOSTLY carry only counts and source IDs (PluralKit HIDs,
    SP `_id`s, etc.), but exception-text branches in
    `build_member` paths could in pathological cases include a value
    that failed Pydantic validation (e.g. a member name on a
    name-field failure). Treat as privacy-sensitive: a reason is
    required and every detail view writes an `import_log_view`
    audit row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_admin_write_user
from sheaf.database import get_db
from sheaf.models.admin_audit_event import AdminAuditAction, AdminAuditTargetType
from sheaf.models.import_job import ImportJob, ImportJobStatus
from sheaf.models.pending_action import PendingAction, PendingActionStatus
from sheaf.models.safety_change_request import (
    SafetyChangeRequest,
    SafetyChangeStatus,
)
from sheaf.models.system import DeleteConfirmation, System
from sheaf.models.user import User
from sheaf.services.admin_audit import log_admin_action
from sheaf.services.sharing import cancel_pending_exposures, pending_exposures
from sheaf.services.system_safety import finalize_pending_action

router = APIRouter(prefix="/admin", tags=["admin emergency"])


async def _system_for_user(user_id: uuid.UUID, db: AsyncSession) -> System:
    """The target's system, 404ing separately for "no such user" and "no system".

    Every endpoint here starts with the same two lookups and the same two
    404s. Factored out because there are now five of them and a sixth would
    have copied whichever version it was sitting next to.
    """
    target_user = await db.get(User, user_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    system = (
        await db.execute(select(System).where(System.user_id == user_id))
    ).scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User has no system",
        )
    return system


# ---------------------------------------------------------------------------
# Reset-safety
# ---------------------------------------------------------------------------

def _safety_category_defaults() -> dict[str, bool]:
    """Every `safety_applies_to_*` column, mapped to the default it declares.

    Read off the model rather than listed by hand, because the hand-written
    list drifted: it was missing `relationships`, `archive` and
    `profile_visibility`, so a reset that advertised itself as clearing all of
    them quietly left three armed. Deriving it means a new category is covered
    the day its column lands instead of the day somebody remembers this file.

    Defaults, not `False`. Fourteen of these gate a DESTRUCTIVE action and
    default off, but `profile_visibility` gates an EXPOSING one and defaults
    ON, so blanket-clearing would use a support ticket about being unable to
    delete something as an excuse to disarm the guard on publishing. Reset
    means "back to how a new account starts", which is the only reading that
    is safe for both directions.
    """
    defaults: dict[str, bool] = {}
    for column in System.__table__.columns:
        if not column.name.startswith("safety_applies_to_"):
            continue
        default = column.default
        defaults[column.name] = bool(default.arg) if default is not None else False
    return defaults


class AdminReasonBody(BaseModel):
    """Free-form reason for the audit log. Required, non-empty."""

    reason: str = Field(min_length=1, max_length=500)


@router.post("/users/{user_id}/reset-safety")
async def reset_system_safety(
    user_id: uuid.UUID,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Put System Safety back to how a new account starts, and cancel any
    queued change that would undo that.

    Zeroes the grace period, sets delete_confirmation back to NONE, and
    returns every `safety_applies_to_*` category to the default its column
    declares. That is off for the fourteen destructive categories and ON for
    `profile_visibility`, which guards exposure rather than destruction:
    re-arming it costs nothing here, because with the grace period at 0 and
    the tier at NONE its step-up verifies nothing, and the owner can turn it
    off from Settings > Safety in one click that applies immediately (a
    loosening only waits when there is a grace period left to wait out).

    Pending SafetyChangeRequest rows are CANCELLED. A deferred loosening the
    owner asked for before the reset would otherwise finalize days later and
    write its stored values straight back over this, handing them a grace
    period again with nothing in the audit log to explain it. The reset
    already puts the account at the loosest setting that request was heading
    for, so cancelling it takes nothing away.

    Two things are deliberately left alone:

    - Queued pending_actions. Anything the owner wants gone now is one cancel
      and one re-delete away, and with the grace period at 0 that re-delete
      lands immediately. Draining the queue instead would finish deletions
      they are still inside the window for, which is the one thing the window
      exists to prevent. Call bypass-pending if they do want it through.
    - Staged exposures. The owner asked to publish; this endpoint is about
      their settings, not their intent. cancel-exposures is the lever for
      those, and GET pending shows what is waiting before anyone presses
      anything."""
    system = await _system_for_user(user_id, db)

    category_defaults = _safety_category_defaults()
    before = {
        "safety_grace_period_days": system.safety_grace_period_days,
        "delete_confirmation": str(system.delete_confirmation.value),
        **{f: getattr(system, f) for f in category_defaults},
    }

    system.safety_grace_period_days = 0
    system.delete_confirmation = DeleteConfirmation.NONE
    for field, default in category_defaults.items():
        setattr(system, field, default)

    after = {
        "safety_grace_period_days": 0,
        "delete_confirmation": str(DeleteConfirmation.NONE.value),
        **category_defaults,
    }
    # Compress the diff: only the fields that actually moved make it
    # into the audit row. If the safeguards were already at their
    # defaults the row is just metadata + reason.
    changed = {k for k in before if before[k] != after[k]}
    diff_before = {k: before[k] for k in changed}
    diff_after = {k: after[k] for k in changed}

    # Cancel queued loosenings, so the reset cannot be reversed by a request
    # the owner made before it. Counted into the same diff rather than logged
    # separately: "this reset also called off 1 queued change" belongs in the
    # row that says what the reset did.
    queued = await db.execute(
        select(SafetyChangeRequest).where(
            SafetyChangeRequest.system_id == system.id,
            SafetyChangeRequest.status == SafetyChangeStatus.PENDING,
        )
    )
    cancelled_changes = list(queued.scalars().all())
    now = datetime.now(UTC)
    for change in cancelled_changes:
        change.status = SafetyChangeStatus.CANCELLED
        change.cancelled_at = now
    if cancelled_changes:
        changed.add("pending_safety_changes")
        diff_before["pending_safety_changes"] = [
            {"id": str(c.id), "changes": c.changes} for c in cancelled_changes
        ]
        diff_after["pending_safety_changes"] = []

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_SAFETY_RESET,
        target_type=AdminAuditTargetType.SYSTEM,
        target_id=system.id,
        target_user_id=user_id,
        reason=body.reason,
        before=diff_before or None,
        after=diff_after or None,
    )
    await db.commit()
    return {"reset": True, "changed_fields": sorted(changed)}


# ---------------------------------------------------------------------------
# What is queued (read, so nobody presses a button blind)
# ---------------------------------------------------------------------------


@router.get("/users/{user_id}/pending")
async def list_user_pending_work(
    user_id: uuid.UUID,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Everything queued on the target's system, in the three shapes it comes in.

    Support had no way to see any of this: they pressed Reset safety or Drain
    pending and read a toast afterwards. Counts and timestamps only, never a
    target's name or a staged value, because which member somebody is deleting
    is not something an admin screen needs to say out loud to answer "is
    anything waiting, and since when".

    A read, so no reason and no audit row: it reveals nothing the owner's own
    Settings > Safety page does not already show them.
    """
    system = await _system_for_user(user_id, db)
    actions = await db.execute(
        select(PendingAction).where(
            PendingAction.system_id == system.id,
            PendingAction.status == PendingActionStatus.PENDING,
        )
    )
    action_rows = list(actions.scalars().all())
    changes = await db.execute(
        select(SafetyChangeRequest).where(
            SafetyChangeRequest.system_id == system.id,
            SafetyChangeRequest.status == SafetyChangeStatus.PENDING,
        )
    )
    change_rows = list(changes.scalars().all())
    exposures = await pending_exposures(system.id, db)

    def _earliest(values: list[datetime | None]) -> str | None:
        real = [v for v in values if v is not None]
        return min(real).isoformat() if real else None

    by_type: dict[str, int] = {}
    for action in action_rows:
        key = str(action.action_type)
        by_type[key] = by_type.get(key, 0) + 1
    by_kind: dict[str, int] = {}
    for exposure in exposures:
        by_kind[exposure.kind] = by_kind.get(exposure.kind, 0) + 1

    return {
        "pending_actions": {
            "count": len(action_rows),
            "by_type": by_type,
            "earliest_finalize_after": _earliest(
                [a.finalize_after for a in action_rows]
            ),
        },
        "pending_changes": {
            "count": len(change_rows),
            "earliest_finalize_after": _earliest(
                [c.finalize_after for c in change_rows]
            ),
        },
        "pending_exposures": {
            "count": len(exposures),
            "by_kind": by_kind,
            "earliest_activates_at": _earliest([e.activates_at for e in exposures]),
        },
    }


# ---------------------------------------------------------------------------
# Cancel staged exposures
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/cancel-exposures")
async def cancel_staged_exposures(
    user_id: uuid.UUID,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Call off every staged flip-to-public raise on the target's system.

    Deliberately its own lever rather than part of reset-safety or of
    bypass-pending, and the asymmetry is the point. Un-exposing is the
    direction this product never gates, so an admin may always stop a
    publication that has not happened yet. Finishing one early is the
    opposite: bypass-pending is a "you asked for this, have it now" for
    deletions, and applying the same logic to exposures would mean a support
    action putting somebody's profile in front of strangers ahead of schedule.
    That is not a thing support should be able to do by pressing the wrong
    button, so nothing folds these two together.

    Staged levels are dropped while live ones are untouched, so this can only
    ever remove a future exposure, never un-publish something already public.
    Idempotent: with nothing staged it reports zero and writes a row saying so.
    """
    system = await _system_for_user(user_id, db)
    cancelled = await cancel_pending_exposures(system.id, db)
    total = sum(cancelled.values())

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_EXPOSURES_CANCELLED,
        target_type=AdminAuditTargetType.SYSTEM,
        target_id=system.id,
        target_user_id=user_id,
        reason=body.reason,
        before=None,
        after={"cancelled_count": total, "by_kind": cancelled},
    )
    await db.commit()
    return {"cancelled_count": total, "by_kind": cancelled}


# ---------------------------------------------------------------------------
# Bypass-pending
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/bypass-pending")
async def bypass_pending_actions(
    user_id: uuid.UUID,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Finalize every pending System Safety action queued on the target
    user's system immediately, bypassing the grace period. Idempotent
    if the queue is empty.

    Writes one user-level USER_PENDING_BYPASS audit row plus one row
    per finalized pending_action so the per-action history is
    recoverable."""
    system = await _system_for_user(user_id, db)

    pending_rows = await db.execute(
        select(PendingAction).where(
            PendingAction.system_id == system.id,
            PendingAction.status == PendingActionStatus.PENDING,
        )
    )
    pending_list = list(pending_rows.scalars().all())

    summary_by_type: dict[str, int] = {}
    for pending in pending_list:
        pending_id = pending.id
        action_type = str(pending.action_type)
        # Force finalize now: drop the grace window by stamping a past
        # finalize_after, so finalize_pending_action's idempotent
        # scope check still applies but the timing gate is moot. We
        # don't bypass the in-scope check because a deletion that
        # would no longer be valid (e.g. target already gone) should
        # still no-op cleanly.
        pending.finalize_after = datetime.now(UTC)
        await finalize_pending_action(pending, db)
        summary_by_type[action_type] = summary_by_type.get(action_type, 0) + 1
        await log_admin_action(
            db,
            admin=admin,
            action=AdminAuditAction.USER_PENDING_BYPASS,
            target_type=AdminAuditTargetType.PENDING_ACTION,
            target_id=pending_id,
            target_user_id=user_id,
            reason=body.reason,
            before={"status": "pending", "action_type": action_type},
            after={"status": str(pending.status), "action_type": action_type},
        )

    # User-level summary row for the audit log's "what did the admin
    # do to me" view — one row that says "drained N pending actions",
    # easier to read than scanning every per-action row.
    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_PENDING_BYPASS,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        before=None,
        after={
            "finalized_count": len(pending_list),
            "by_type": summary_by_type,
        },
    )

    await db.commit()
    return {
        "finalized_count": len(pending_list),
        "by_type": summary_by_type,
    }


# ---------------------------------------------------------------------------
# Import-job log view (privacy-sensitive read, logged)
# ---------------------------------------------------------------------------

class _ImportJobSummary(BaseModel):
    id: uuid.UUID
    source: str
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    counts: dict
    last_error: str | None


class _ImportJobDetail(_ImportJobSummary):
    events: list[dict]


@router.get(
    "/users/{user_id}/import-jobs",
    response_model=list[_ImportJobSummary],
)
async def list_user_import_jobs(
    user_id: uuid.UUID,
    _: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """List import jobs for a target user, most-recent first. Browse
    only — events are NOT returned and no audit row is written here.
    """
    target_user = await db.get(User, user_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    rows = await db.execute(
        select(ImportJob)
        .where(ImportJob.user_id == user_id)
        .order_by(desc(ImportJob.created_at))
        .limit(100)
    )
    out: list[_ImportJobSummary] = []
    for j in rows.scalars().all():
        out.append(
            _ImportJobSummary(
                id=j.id,
                source=str(j.source),
                status=str(j.status),
                created_at=j.created_at,
                started_at=j.started_at,
                finished_at=j.finished_at,
                counts=j.counts or {},
                last_error=j.last_error,
            )
        )
    return out


@router.post(
    "/import-jobs/{job_id}",
    response_model=_ImportJobDetail,
)
async def view_import_job_detail(
    job_id: uuid.UUID,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the full event log of a single import job.

    POST + body rather than GET + query so the reason is captured in
    a request body, not a URL that ends up in proxy / browser-history
    logs. Writes an `import_log_view` audit row so the user can see
    when an admin looked at their import history.

    Importer events are mostly structural (counts, importer state,
    PluralKit HIDs / SP `_id`s as `record_ref`) but exception-text
    branches in `build_member`-style paths can in pathological cases
    quote a value that failed Pydantic validation. Treating them as
    privacy-sensitive on read keeps the user's account transparent."""
    job = await db.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import job not found",
        )
    # Log BEFORE returning so a failed-but-completed read still
    # creates the row.
    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.IMPORT_LOG_VIEW,
        target_type=AdminAuditTargetType.IMPORT_JOB,
        target_id=job.id,
        target_user_id=job.user_id,
        reason=body.reason,
        before=None,
        after={
            "source": str(job.source),
            "status": str(job.status),
            "event_count": len(job.events or []),
        },
    )
    await db.commit()
    return _ImportJobDetail(
        id=job.id,
        source=str(job.source),
        status=str(job.status),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        counts=job.counts or {},
        last_error=job.last_error,
        events=job.events or [],
    )


# Suppress unused-import warning: ImportJobStatus is reserved for an
# upcoming filter on list_user_import_jobs (active vs terminal).
_ = ImportJobStatus

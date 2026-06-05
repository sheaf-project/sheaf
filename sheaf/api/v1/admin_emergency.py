"""Admin emergency-support endpoints.

Three actions support operators can take against a user account, all
gated behind admin auth + a required reason string, all logged in the
admin audit table:

  - POST /admin/users/{id}/reset-safety: clear all System Safety
    toggles and zero the grace period. Use when a user accidentally
    locks themselves out with strict safeguards. Does NOT touch
    already-queued pending_actions — bypass-pending does that.

  - POST /admin/users/{id}/bypass-pending: finalize every pending
    System Safety action on the user's system NOW, without waiting
    out the grace period. Use when a user has stuck deletions in the
    queue and wants them through right away.

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
from sheaf.models.system import DeleteConfirmation, System
from sheaf.models.user import User
from sheaf.services.admin_audit import log_admin_action
from sheaf.services.system_safety import finalize_pending_action

router = APIRouter(prefix="/admin", tags=["admin emergency"])


# ---------------------------------------------------------------------------
# Reset-safety
# ---------------------------------------------------------------------------

_SAFETY_TOGGLE_FIELDS = (
    "safety_applies_to_members",
    "safety_applies_to_groups",
    "safety_applies_to_tags",
    "safety_applies_to_fields",
    "safety_applies_to_fronts",
    "safety_applies_to_journals",
    "safety_applies_to_images",
    "safety_applies_to_revisions",
    "safety_applies_to_notifications",
    "safety_applies_to_reminders",
    "safety_applies_to_polls",
    "safety_applies_to_messages",
)


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
    """Clear all System Safety category toggles, zero the grace period,
    and set delete_confirmation back to NONE on the target user's
    system. Future destructive actions on the account are no longer
    safeguarded; the user can re-enable safeguards at any time from
    Settings > Safety. Already-queued pending actions are NOT touched
    here — call bypass-pending for those."""
    target_user = await db.get(User, user_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    sys_row = await db.execute(
        select(System).where(System.user_id == user_id)
    )
    system = sys_row.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User has no system",
        )

    before = {
        "safety_grace_period_days": system.safety_grace_period_days,
        "delete_confirmation": str(system.delete_confirmation.value),
        **{f: getattr(system, f) for f in _SAFETY_TOGGLE_FIELDS},
    }

    system.safety_grace_period_days = 0
    system.delete_confirmation = DeleteConfirmation.NONE
    for f in _SAFETY_TOGGLE_FIELDS:
        setattr(system, f, False)

    after = {
        "safety_grace_period_days": 0,
        "delete_confirmation": str(DeleteConfirmation.NONE.value),
        **{f: False for f in _SAFETY_TOGGLE_FIELDS},
    }
    # Compress the diff: only the fields that actually moved make it
    # into the audit row. If the safeguards were already all off the
    # row is just metadata + reason.
    changed = {k for k in before if before[k] != after[k]}
    diff_before = {k: before[k] for k in changed}
    diff_after = {k: after[k] for k in changed}

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
    target_user = await db.get(User, user_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    sys_row = await db.execute(
        select(System).where(System.user_id == user_id)
    )
    system = sys_row.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User has no system",
        )

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

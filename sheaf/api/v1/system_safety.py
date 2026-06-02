"""System Safety API endpoints.

External field names (`grace_period_days`, `auth_tier`, `applies_to_*`) are
mapped to the internal column names (`safety_grace_period_days`,
`delete_confirmation`, `safety_applies_to_*`). `auth_tier` is the historical
`delete_confirmation` column — see sheaf/models/system.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.api.v1.members import _get_user_system
from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.pending_action import PendingAction, PendingActionStatus
from sheaf.models.safety_change_request import (
    SafetyChangeRequest,
    SafetyChangeStatus,
)
from sheaf.models.system import DeleteConfirmation, System
from sheaf.models.user import User
from sheaf.observability.metrics import pending_actions_finalized_total
from sheaf.schemas.system_safety import (
    PendingActionRead,
    SafetyChangeRequestRead,
    SystemSafetyResponse,
    SystemSafetySettings,
    SystemSafetyUpdate,
    SystemSafetyUpdateResponse,
)
from sheaf.services.system_safety import (
    split_safety_changes,
    verify_destructive_auth,
)

router = APIRouter(prefix="/system/safety", tags=["system-safety"])


# External -> internal field name translation. Kept here (not in the service)
# because only the API surface cares about the nicer external naming.
_EXTERNAL_TO_INTERNAL = {
    "grace_period_days": "safety_grace_period_days",
    "auth_tier": "delete_confirmation",
    "applies_to_members": "safety_applies_to_members",
    "applies_to_groups": "safety_applies_to_groups",
    "applies_to_tags": "safety_applies_to_tags",
    "applies_to_fields": "safety_applies_to_fields",
    "applies_to_fronts": "safety_applies_to_fronts",
    "applies_to_journals": "safety_applies_to_journals",
    "applies_to_images": "safety_applies_to_images",
    "applies_to_revisions": "safety_applies_to_revisions",
    "applies_to_notifications": "safety_applies_to_notifications",
    "applies_to_reminders": "safety_applies_to_reminders",
    "applies_to_polls": "safety_applies_to_polls",
    "applies_to_messages": "safety_applies_to_messages",
    "auto_pin_first_revision": "auto_pin_first_revision",
}
_INTERNAL_TO_EXTERNAL = {v: k for k, v in _EXTERNAL_TO_INTERNAL.items()}


def _settings_from_system(system: System) -> SystemSafetySettings:
    return SystemSafetySettings(
        grace_period_days=system.safety_grace_period_days,
        auth_tier=system.delete_confirmation,
        applies_to_members=system.safety_applies_to_members,
        applies_to_groups=system.safety_applies_to_groups,
        applies_to_tags=system.safety_applies_to_tags,
        applies_to_fields=system.safety_applies_to_fields,
        applies_to_fronts=system.safety_applies_to_fronts,
        applies_to_journals=system.safety_applies_to_journals,
        applies_to_images=system.safety_applies_to_images,
        applies_to_revisions=system.safety_applies_to_revisions,
        applies_to_notifications=system.safety_applies_to_notifications,
        applies_to_reminders=system.safety_applies_to_reminders,
        applies_to_polls=system.safety_applies_to_polls,
        applies_to_messages=system.safety_applies_to_messages,
        auto_pin_first_revision=system.auto_pin_first_revision,
    )


async def _load_pending(
    system_id: uuid.UUID, db: AsyncSession
) -> tuple[list[PendingAction], list[SafetyChangeRequest]]:
    actions_result = await db.execute(
        select(PendingAction)
        .where(
            PendingAction.system_id == system_id,
            PendingAction.status == PendingActionStatus.PENDING,
        )
        .order_by(PendingAction.finalize_after)
    )
    changes_result = await db.execute(
        select(SafetyChangeRequest)
        .where(
            SafetyChangeRequest.system_id == system_id,
            SafetyChangeRequest.status == SafetyChangeStatus.PENDING,
        )
        .order_by(SafetyChangeRequest.finalize_after)
    )
    return list(actions_result.scalars().all()), list(changes_result.scalars().all())


@router.get("", response_model=SystemSafetyResponse)
async def get_system_safety(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    actions, changes = await _load_pending(system.id, db)
    return SystemSafetyResponse(
        settings=_settings_from_system(system),
        pending_actions=[PendingActionRead.model_validate(a) for a in actions],
        pending_changes=[SafetyChangeRequestRead.model_validate(c) for c in changes],
    )


@router.patch(
    "",
    response_model=SystemSafetyUpdateResponse,
    dependencies=[Depends(require_scope("system:write"))],
)
async def update_system_safety(
    body: SystemSafetyUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)

    external_updates = body.model_dump(
        exclude_none=True, exclude={"password", "totp_code"}
    )
    internal_updates = {
        _EXTERNAL_TO_INTERNAL[k]: v
        for k, v in external_updates.items()
        if k in _EXTERNAL_TO_INTERNAL
    }
    # Don't let the auth tier be raised to a TOTP-requiring level unless the
    # user actually has TOTP enrolled — otherwise the gate would be a no-op
    # and silently wave destructive actions through. Mirrors the dedicated
    # /v1/systems delete-confirmation endpoint.
    new_tier = internal_updates.get("delete_confirmation")
    if (
        new_tier in (DeleteConfirmation.TOTP, DeleteConfirmation.BOTH)
        and not user.totp_enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot require TOTP confirmation without 2FA enabled",
        )

    split = split_safety_changes(system, internal_updates)

    # Re-auth gate applies to any loosening change. For tightening-only changes
    # we skip re-auth — there's no attack surface in strengthening protection.
    if split.deferred:
        verify_destructive_auth(user, system, body.password, body.totp_code)

    # Apply tightening immediately.
    for field, value in split.applied.items():
        setattr(system, field, value)

    pending_change: SafetyChangeRequest | None = None
    if split.deferred:
        now = datetime.now(UTC)
        # Grace period for loosening is whatever is currently in force.
        # If grace is 0 (safety fully off), loosening applies immediately too.
        if system.safety_grace_period_days <= 0:
            for field, value in split.deferred.items():
                setattr(system, field, value)
            split.applied.update(split.deferred)
            split.deferred.clear()
        else:
            pending_change = SafetyChangeRequest(
                system_id=system.id,
                requested_at=now,
                requested_by_user_id=user.id,
                finalize_after=now + timedelta(days=system.safety_grace_period_days),
                changes=split.deferred,
                status=SafetyChangeStatus.PENDING,
            )
            db.add(pending_change)

    await db.commit()
    await db.refresh(system)
    if pending_change is not None:
        await db.refresh(pending_change)

    return SystemSafetyUpdateResponse(
        settings=_settings_from_system(system),
        applied=[_INTERNAL_TO_EXTERNAL.get(f, f) for f in split.applied],
        deferred=[_INTERNAL_TO_EXTERNAL.get(f, f) for f in split.deferred],
        pending_change=(
            SafetyChangeRequestRead.model_validate(pending_change)
            if pending_change is not None
            else None
        ),
    )


@router.delete(
    "/pending-actions/{pending_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("system:write"))],
)
async def cancel_pending_action(
    pending_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    pending = await db.get(PendingAction, pending_id)
    if pending is None or pending.system_id != system.id:
        raise HTTPException(status_code=404, detail="Pending action not found")
    if pending.status != PendingActionStatus.PENDING:
        raise HTTPException(status_code=400, detail="Not pending")
    pending.status = PendingActionStatus.CANCELLED
    pending.cancelled_at = datetime.now(UTC)
    pending.cancelled_by_user_id = user.id
    await db.commit()
    pending_actions_finalized_total.labels(
        category=pending.action_type, outcome="cancelled",
    ).inc()


@router.delete(
    "/pending-changes/{change_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("system:write"))],
)
async def cancel_pending_change(
    change_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    change = await db.get(SafetyChangeRequest, change_id)
    if change is None or change.system_id != system.id:
        raise HTTPException(status_code=404, detail="Pending change not found")
    if change.status != SafetyChangeStatus.PENDING:
        raise HTTPException(status_code=400, detail="Not pending")
    change.status = SafetyChangeStatus.CANCELLED
    change.cancelled_at = datetime.now(UTC)
    await db.commit()

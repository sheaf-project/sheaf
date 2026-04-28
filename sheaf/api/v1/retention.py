"""Revision-history retention API.

Exposes effective + tier-max + override caps for revision retention, the
loosening-aware update endpoint, and the active tier-downgrade trim notice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.api.v1.members import _get_user_system
from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.retention_trim_notice import (
    RetentionTrimNotice,
    RetentionTrimStatus,
)
from sheaf.models.safety_change_request import (
    SafetyChangeRequest,
    SafetyChangeStatus,
)
from sheaf.models.user import User
from sheaf.schemas.retention import (
    RetentionResponse,
    RetentionTrimNoticeRead,
    RetentionUpdate,
)
from sheaf.services.journals import effective_revision_caps, tier_revision_caps
from sheaf.services.retention import get_active_trim_notice
from sheaf.services.system_safety import (
    split_safety_changes,
    verify_destructive_auth,
)

router = APIRouter(prefix="/retention", tags=["retention"])


def _coerce_override(value: int | None, tier_max: int) -> int | None:
    """Reject overrides higher than the tier max. 0 means unlimited.

    None passes through (clears the override).
    """
    if value is None:
        return None
    if tier_max == 0:
        return value
    if value == 0 or value > tier_max:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Override exceeds tier maximum ({tier_max})",
        )
    return value


@router.get("", response_model=RetentionResponse)
async def get_retention(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    tier_rev, tier_days = tier_revision_caps(user.tier)
    eff_rev, eff_days = effective_revision_caps(user, system)
    notice = await get_active_trim_notice(user.id, db)
    return RetentionResponse(
        effective_max_revisions=eff_rev,
        effective_max_days=eff_days,
        tier_max_revisions=tier_rev,
        tier_max_days=tier_days,
        override_revisions=system.journal_max_revisions,
        override_days=system.journal_max_revision_days,
        trim_notice=(
            RetentionTrimNoticeRead.model_validate(notice) if notice else None
        ),
    )


@router.patch(
    "",
    response_model=RetentionResponse,
    dependencies=[Depends(require_scope("system:write"))],
)
async def update_retention(
    body: RetentionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    tier_rev, tier_days = tier_revision_caps(user.tier)

    # Unlike SystemSafetyUpdate, we want to honor explicit None as "clear
    # override", so we use exclude_unset rather than exclude_none.
    sent = body.model_dump(exclude_unset=True, exclude={"password", "totp_code"})

    proposed: dict[str, int | None] = {}
    if "max_revisions" in sent:
        proposed["journal_max_revisions"] = _coerce_override(
            sent["max_revisions"], tier_rev
        )
    if "max_revision_days" in sent:
        proposed["journal_max_revision_days"] = _coerce_override(
            sent["max_revision_days"], tier_days
        )

    split = split_safety_changes(system, proposed)

    if split.deferred:
        verify_destructive_auth(user, system, body.password, body.totp_code)

    # Apply tightening immediately.
    for field, value in split.applied.items():
        setattr(system, field, value)

    if split.deferred:
        if system.safety_grace_period_days <= 0:
            # No grace period configured — apply loosening immediately too.
            for field, value in split.deferred.items():
                setattr(system, field, value)
        else:
            now = datetime.now(UTC)
            db.add(
                SafetyChangeRequest(
                    system_id=system.id,
                    requested_at=now,
                    requested_by_user_id=user.id,
                    finalize_after=now
                    + timedelta(days=system.safety_grace_period_days),
                    changes=split.deferred,
                    status=SafetyChangeStatus.PENDING,
                )
            )

    await db.commit()
    await db.refresh(system)

    eff_rev, eff_days = effective_revision_caps(user, system)
    notice = await get_active_trim_notice(user.id, db)
    return RetentionResponse(
        effective_max_revisions=eff_rev,
        effective_max_days=eff_days,
        tier_max_revisions=tier_rev,
        tier_max_days=tier_days,
        override_revisions=system.journal_max_revisions,
        override_days=system.journal_max_revision_days,
        trim_notice=(
            RetentionTrimNoticeRead.model_validate(notice) if notice else None
        ),
    )


@router.delete(
    "/trim-notice/{notice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("system:write"))],
)
async def cancel_trim_notice(
    notice_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a pending trim notice. Rare — usually cancelled implicitly by
    re-upgrade (`on_tier_change`)."""
    notice = await db.get(RetentionTrimNotice, notice_id)
    if notice is None or notice.user_id != user.id:
        raise HTTPException(status_code=404, detail="Trim notice not found")
    if notice.status != RetentionTrimStatus.PENDING:
        raise HTTPException(status_code=400, detail="Not pending")
    notice.status = RetentionTrimStatus.CANCELLED
    notice.cancelled_at = datetime.now(UTC)
    await db.commit()

"""System Safety service layer.

Contains:
- Auth helper shared by every destructive action endpoint.
- Loosening detection for safety-setting changes (asymmetric delay).
- Fronting snapshot helper used when queuing pending actions.
- Dispatcher for finalizing pending actions against the right model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.passwords import verify_password
from sheaf.auth.totp import verify_code
from sheaf.crypto import decrypt
from sheaf.models.custom_field import CustomFieldDefinition
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.member import Member
from sheaf.models.pending_action import (
    PendingAction,
    PendingActionStatus,
    PendingActionType,
)
from sheaf.models.safety_change_request import (
    SafetyChangeRequest,
    SafetyChangeStatus,
)
from sheaf.models.system import DeleteConfirmation, System
from sheaf.models.tag import Tag
from sheaf.models.user import User

# Categories that safety can apply to — kept in one place so the API,
# schemas, and finalize dispatcher all agree on the set.
SAFETY_CATEGORIES: tuple[str, ...] = (
    "members",
    "groups",
    "tags",
    "fields",
    "fronts",
)

_CATEGORY_BY_ACTION: dict[str, str] = {
    PendingActionType.MEMBER_DELETE: "members",
    PendingActionType.GROUP_DELETE: "groups",
    PendingActionType.TAG_DELETE: "tags",
    PendingActionType.FIELD_DELETE: "fields",
    PendingActionType.FRONT_DELETE: "fronts",
}

_MODEL_BY_ACTION: dict[str, type] = {
    PendingActionType.MEMBER_DELETE: Member,
    PendingActionType.GROUP_DELETE: Group,
    PendingActionType.TAG_DELETE: Tag,
    PendingActionType.FIELD_DELETE: CustomFieldDefinition,
    PendingActionType.FRONT_DELETE: Front,
}


# Auth tier strength — PASSWORD and TOTP are treated as equivalent
# strength (either-or, not both). NONE < PASSWORD/TOTP < BOTH.
_TIER_STRENGTH: dict[str, int] = {
    DeleteConfirmation.NONE: 0,
    DeleteConfirmation.PASSWORD: 1,
    DeleteConfirmation.TOTP: 1,
    DeleteConfirmation.BOTH: 2,
}


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def verify_destructive_auth(
    user: User,
    system: System,
    password: str | None,
    totp_code: str | None,
) -> None:
    """Raise HTTPException if the required re-auth for destructive actions isn't satisfied.

    Mirrors the existing member-delete pattern. Called at the top of every
    safeguarded destructive endpoint and again before finalizing safety-setting
    loosening changes.
    """
    level = system.delete_confirmation

    if level in (DeleteConfirmation.PASSWORD, DeleteConfirmation.BOTH) and (
        not password or not verify_password(password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password required",
        )

    if level in (DeleteConfirmation.TOTP, DeleteConfirmation.BOTH):
        if not user.totp_enabled:
            # TOTP gate requested but not configured — skip silently, as
            # elsewhere in the codebase (members.py:154).
            return
        if not totp_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TOTP code required",
            )
        secret = decrypt(user.totp_secret)
        if not verify_code(secret, totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid TOTP code",
            )


# ---------------------------------------------------------------------------
# Safety settings change detection (asymmetric loosening delay)
# ---------------------------------------------------------------------------


@dataclass
class SafetyChangeSplit:
    applied: dict[str, Any]
    deferred: dict[str, Any]


def split_safety_changes(system: System, updates: dict[str, Any]) -> SafetyChangeSplit:
    """Split proposed updates into immediate (tighten) vs. deferred (loosen).

    Loosening means:
      - grace period reduced
      - delete_confirmation tier strength reduced
      - any safety_applies_to_<category> flipped True -> False
    """
    applied: dict[str, Any] = {}
    deferred: dict[str, Any] = {}

    for field, new_value in updates.items():
        if new_value is None:
            continue
        current = getattr(system, field, None)
        if current == new_value:
            continue

        if field == "safety_grace_period_days":
            if new_value < current:
                deferred[field] = new_value
            else:
                applied[field] = new_value
        elif field == "delete_confirmation":
            new_strength = _TIER_STRENGTH.get(new_value, 0)
            old_strength = _TIER_STRENGTH.get(current, 0)
            if new_strength < old_strength:
                deferred[field] = new_value
            else:
                applied[field] = new_value
        elif field.startswith("safety_applies_to_"):
            if current is True and new_value is False:
                deferred[field] = new_value
            else:
                applied[field] = new_value
        else:
            # Unknown field — apply immediately (router should reject unknowns upstream).
            applied[field] = new_value

    return SafetyChangeSplit(applied=applied, deferred=deferred)


# ---------------------------------------------------------------------------
# Fronting snapshot
# ---------------------------------------------------------------------------


async def snapshot_current_fronts(
    system_id: uuid.UUID, db: AsyncSession
) -> tuple[list[str], list[str]]:
    """Return (member_ids, member_names) for members currently fronting."""
    result = await db.execute(
        select(Member)
        .join(Member.fronts)
        .where(Front.system_id == system_id, Front.ended_at.is_(None))
        .distinct()
    )
    members = result.scalars().all()
    return (
        [str(m.id) for m in members],
        [m.display_name or m.name for m in members],
    )


# ---------------------------------------------------------------------------
# Queue pending action
# ---------------------------------------------------------------------------


def is_safeguarded(system: System, action_type: str) -> bool:
    """True if grace > 0 AND the category toggle is on for this action type."""
    if system.safety_grace_period_days <= 0:
        return False
    category = _CATEGORY_BY_ACTION.get(action_type)
    if category is None:
        return False
    return bool(getattr(system, f"safety_applies_to_{category}"))


async def queue_pending_action(
    *,
    db: AsyncSession,
    system: System,
    user: User,
    action_type: str,
    target_id: uuid.UUID,
    target_label: str,
) -> PendingAction:
    """Create a PendingAction row with a fronting snapshot. Caller commits."""
    fronting_ids, fronting_names = await snapshot_current_fronts(system.id, db)
    now = datetime.now(UTC)
    pending = PendingAction(
        system_id=system.id,
        action_type=action_type,
        target_id=target_id,
        target_label=target_label,
        requested_at=now,
        requested_by_user_id=user.id,
        finalize_after=now + timedelta(days=system.safety_grace_period_days),
        fronting_member_ids=fronting_ids,
        fronting_member_names=fronting_names,
        status=PendingActionStatus.PENDING,
    )
    db.add(pending)
    return pending


def has_pending_action_for_target(
    db_actions: list[PendingAction], target_id: uuid.UUID, action_type: str
) -> bool:
    """Check a preloaded list for an existing pending action on this target."""
    return any(
        p.target_id == target_id
        and p.action_type == action_type
        and p.status == PendingActionStatus.PENDING
        for p in db_actions
    )


# ---------------------------------------------------------------------------
# Finalize pending action
# ---------------------------------------------------------------------------


async def finalize_pending_action(
    pending: PendingAction, db: AsyncSession
) -> None:
    """Execute the queued delete. Idempotent: missing target marks completed."""
    model = _MODEL_BY_ACTION.get(pending.action_type)
    if model is None:
        pending.status = PendingActionStatus.ERRORED
        pending.error_message = f"Unknown action_type: {pending.action_type}"
        pending.completed_at = datetime.now(UTC)
        return

    target = await db.get(model, pending.target_id)
    # Verify scope — guard against target reparented somehow.
    if target is not None and getattr(target, "system_id", None) == pending.system_id:
        await db.delete(target)

    pending.status = PendingActionStatus.COMPLETED
    pending.completed_at = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Finalize safety-setting changes
# ---------------------------------------------------------------------------


async def finalize_safety_change(
    request: SafetyChangeRequest, db: AsyncSession
) -> None:
    """Apply a deferred safety-setting loosening to the system."""
    system = await db.get(System, request.system_id)
    if system is not None:
        for field, value in (request.changes or {}).items():
            if hasattr(system, field):
                setattr(system, field, value)
    request.status = SafetyChangeStatus.COMPLETED
    request.completed_at = datetime.now(UTC)

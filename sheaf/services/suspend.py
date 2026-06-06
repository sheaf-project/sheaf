"""Suspend / unsuspend bookkeeping shared between the admin endpoints
and the background sweep.

Both callers need to do the same DB write + session revocation: the
admin endpoint when an operator suspends manually, and the sweep when
an expired suspension fires. Centralising here keeps the audit row's
`before_json` shape consistent across the two.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.sessions import delete_all_user_sessions
from sheaf.models.user import AccountStatus, User


async def apply_suspend(
    db: AsyncSession,
    target: User,
    *,
    until: datetime | None,
    reason: str,
) -> dict[str, object]:
    """Move a user into SUSPENDED state and revoke their sessions.

    Caller commits. Returns a `before_json`-shaped dict capturing the
    pre-suspension state, so the audit row can record it without the
    caller re-computing.
    """
    before = {
        "account_status": str(target.account_status),
        "suspended_until": (
            target.suspended_until.isoformat()
            if target.suspended_until is not None
            else None
        ),
        "suspended_reason": target.suspended_reason,
    }
    target.account_status = AccountStatus.SUSPENDED
    target.suspended_until = until
    target.suspended_reason = reason
    revoked = 0
    try:
        revoked = await delete_all_user_sessions(target.id)
    except Exception:
        # Session revocation is best-effort: the auth dep + login gate
        # will still refuse the suspended user even if Redis is down.
        # Surfacing the failure to the caller would falsely roll back
        # the DB-side suspension.
        revoked = -1
    before["_sessions_revoked"] = revoked
    return before


async def apply_unsuspend(
    db: AsyncSession,
    target: User,
) -> dict[str, object]:
    """Lift a SUSPENDED account back to ACTIVE.

    No-op if the user is not currently suspended. Caller commits.
    Returns a before-state dict (timestamp + reason) so the caller can
    pin them in the audit row.
    """
    before = {
        "account_status": str(target.account_status),
        "suspended_until": (
            target.suspended_until.isoformat()
            if target.suspended_until is not None
            else None
        ),
        "suspended_reason": target.suspended_reason,
    }
    if target.account_status != AccountStatus.SUSPENDED:
        return before
    target.account_status = AccountStatus.ACTIVE
    target.suspended_until = None
    target.suspended_reason = None
    return before


async def sweep_expired_suspensions(db: AsyncSession) -> int:
    """Find users whose `suspended_until` has passed and lift them.

    Called by the periodic job runner. Returns the number of users
    restored. Writes USER_UNSUSPEND audit rows with `admin_user_id`
    NULL so the sweep is distinguishable from a manual unsuspend in
    the audit trail.
    """
    from sqlalchemy import select

    from sheaf.models.admin_audit_event import (
        AdminAuditAction,
        AdminAuditEvent,
        AdminAuditTargetType,
    )

    now = datetime.now(UTC)
    rows = await db.execute(
        select(User)
        .where(
            User.account_status == AccountStatus.SUSPENDED,
            User.suspended_until.is_not(None),
            User.suspended_until <= now,
        )
        .with_for_update(skip_locked=True)
    )
    targets = list(rows.scalars().all())
    for target in targets:
        before = await apply_unsuspend(db, target)
        event = AdminAuditEvent(
            id=uuid.uuid4(),
            admin_user_id=None,
            action=AdminAuditAction.USER_UNSUSPEND,
            target_type=AdminAuditTargetType.USER,
            target_id=target.id,
            target_user_id=target.id,
            reason="auto-unsuspend at expiry",
            before_json=before,
            after_json={"account_status": "active"},
            created_at=now,
            admin_email=None,
        )
        db.add(event)
    return len(targets)

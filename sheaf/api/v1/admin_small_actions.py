"""Admin small-actions batch (PR 3).

A grab-bag of tightly scoped admin endpoints that don't fit cleanly
in admin.py (which is already the kitchen sink) or admin_emergency.py
(which is reserved for the three break-glass operations: reset-safety,
bypass-pending, import-log view). Splitting these out keeps the surface
area surveyable.

Endpoints:

  - GET /admin/users/{id}/explain
        One-shot dossier the operator can glance at when a user reports
        a problem. Pure read; no audit row written. Aggregates basic
        account fields, system metadata, session count, API-key count,
        and the most recent admin audit rows touching this account.

  - POST /admin/users/{id}/sessions/{sid}/terminate
        Revoke a single session. Reason required; logged.

  - POST /admin/users/{id}/api-keys/rotate-all
        Revoke every API key the user owns. Reason required; logged.
        Use when a user reports a leak and asks for a hard reset.

  - POST /admin/approvals/bulk-approve
        Approve a list of pending-approval users in one shot. Each
        approved user gets the same per-user USER_APPROVE row the
        single-approve endpoint would write, so the audit log stays
        grain-consistent — operators can still filter by user.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_admin_user, get_admin_write_user
from sheaf.auth.sessions import (
    delete_session,
    get_session_info,
    list_user_sessions,
)
from sheaf.crypto import decrypt_field
from sheaf.database import get_db
from sheaf.models.admin_audit_event import (
    AdminAuditAction,
    AdminAuditEvent,
    AdminAuditTargetType,
)
from sheaf.models.api_key import ApiKey
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.user import AccountStatus, User
from sheaf.services.admin_audit import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin small actions"])


class AdminReasonBody(BaseModel):
    """Required free-form reason captured on every audit row."""

    reason: str = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# Explain account (read-only dossier)
# ---------------------------------------------------------------------------


class _ExplainSystem(BaseModel):
    id: uuid.UUID
    name: str
    member_count: int
    delete_confirmation: str
    grace_period_days: int


class _ExplainRecentAudit(BaseModel):
    id: uuid.UUID
    action: str
    target_type: str
    reason: str | None
    created_at: datetime


class ExplainAccountResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    tier: str
    is_admin: bool
    account_status: str
    email_verified: bool
    totp_enabled: bool
    signup_ip: str | None
    created_at: datetime
    last_login_at: datetime | None

    active_session_count: int
    api_key_count: int
    system: _ExplainSystem | None

    # Most recent admin actions that touched this account, including
    # actions taken by other admins. Bounded to keep the payload sane.
    recent_admin_audit: list[_ExplainRecentAudit]


@router.get(
    "/users/{user_id}/explain",
    response_model=ExplainAccountResponse,
)
async def explain_account(
    user_id: uuid.UUID,
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Dossier view for triage. Read-only — does not write an audit row.

    Aggregates information that would otherwise require five separate
    admin endpoints to assemble. Deliberately omits anything the user
    hasn't already exposed to the system (no decrypted content from
    members / journals / messages, only counts and structural state)."""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    try:
        email = decrypt_field(target.email, "email")
    except Exception:
        email = "<encrypted>"

    sys_row = await db.execute(select(System).where(System.user_id == user_id))
    system = sys_row.scalar_one_or_none()
    system_block: _ExplainSystem | None = None
    if system is not None:
        member_count_row = await db.execute(
            select(func.count(Member.id)).where(Member.system_id == system.id)
        )
        member_count = member_count_row.scalar_one()
        system_block = _ExplainSystem(
            id=system.id,
            name=system.name or "",
            member_count=member_count,
            delete_confirmation=str(system.delete_confirmation.value),
            grace_period_days=system.safety_grace_period_days,
        )

    api_key_count_row = await db.execute(
        select(func.count(ApiKey.id)).where(ApiKey.user_id == user_id)
    )
    api_key_count = api_key_count_row.scalar_one()

    sessions = await list_user_sessions(user_id)

    audit_rows = await db.execute(
        select(AdminAuditEvent)
        .where(AdminAuditEvent.target_user_id == user_id)
        .order_by(desc(AdminAuditEvent.created_at))
        .limit(20)
    )
    recent_audit = [
        _ExplainRecentAudit(
            id=row.id,
            action=str(row.action),
            target_type=str(row.target_type),
            reason=row.reason,
            created_at=row.created_at,
        )
        for row in audit_rows.scalars().all()
    ]

    return ExplainAccountResponse(
        user_id=target.id,
        email=email,
        tier=str(target.tier.value),
        is_admin=target.is_admin,
        account_status=str(target.account_status),
        email_verified=target.email_verified,
        totp_enabled=target.totp_enabled,
        signup_ip=target.signup_ip,
        created_at=target.created_at,
        last_login_at=target.last_login_at,
        active_session_count=len(sessions),
        api_key_count=api_key_count,
        system=system_block,
        recent_admin_audit=recent_audit,
    )


# ---------------------------------------------------------------------------
# List a user's sessions (pure read; no audit row)
# ---------------------------------------------------------------------------


class _SessionRow(BaseModel):
    id: str
    user_agent: str | None = None
    ip: str | None = None
    created_at: str | None = None
    last_seen_at: str | None = None
    nickname: str | None = None


@router.get(
    "/users/{user_id}/sessions",
    response_model=list[_SessionRow],
)
async def list_user_sessions_endpoint(
    user_id: uuid.UUID,
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List a target user's active sessions. Read-only; not logged.

    Companion to /terminate so the admin can see what's in the session
    set before deciding which one to kill. Same shape as the
    user-facing /auth/sessions response, except sourced from another
    user's id rather than the request's own JWT.
    """
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    raw = await list_user_sessions(user_id)
    return [
        _SessionRow(
            id=row.get("id", ""),
            user_agent=row.get("user_agent"),
            ip=row.get("ip"),
            created_at=row.get("created_at"),
            last_seen_at=row.get("last_seen_at"),
            nickname=row.get("nickname"),
        )
        for row in raw
    ]


# ---------------------------------------------------------------------------
# Terminate a single session
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/sessions/{target_session_id}/terminate")
async def terminate_user_session(
    user_id: uuid.UUID,
    target_session_id: str,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke one of a user's sessions out-of-band.

    The session id must already belong to the target user — otherwise
    the request 404s without revealing which side of the mismatch was
    wrong. Writes a USER_SESSION_REVOKE audit row with the captured
    user_agent / created_at / ip (whatever the session record had).

    Path param is `target_session_id` rather than `session_id` to
    avoid colliding with the `session_id` cookie alias resolved
    transitively through the auth dependency chain — FastAPI raises if
    the same name appears as both a path param and a `Cookie(...)` in
    the dep tree.
    """
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    info = await get_session_info(target_session_id)
    if info is None or info.get("user_id") != str(user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    snapshot = {
        "session_id": target_session_id,
        "user_agent": info.get("user_agent"),
        "ip": info.get("ip"),
        "created_at": info.get("created_at"),
    }

    await delete_session(target_session_id)

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_SESSION_REVOKE,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        before=snapshot,
        after={"session_id": target_session_id, "revoked": True},
    )
    await db.commit()
    return {"revoked": True}


# ---------------------------------------------------------------------------
# Force-rotate API keys
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/api-keys/rotate-all")
async def force_rotate_api_keys(
    user_id: uuid.UUID,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke every API key on the target account.

    Idempotent: if the user has zero keys, returns revoked_count=0 and
    still writes an audit row (so the operator's attempt is recorded
    even when there was nothing to rotate). Does NOT mint replacement
    keys — the user reissues from their own settings page after the
    rotation, so admins never touch fresh secret material.
    """
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    keys_row = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user_id)
    )
    keys = list(keys_row.scalars().all())
    snapshot = [{"id": str(k.id), "name": k.name} for k in keys]

    for k in keys:
        await db.delete(k)

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_API_KEYS_ROTATE_ALL,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        before={"keys": snapshot} if snapshot else None,
        after={"revoked_count": len(keys)},
    )
    await db.commit()
    return {"revoked_count": len(keys)}


# ---------------------------------------------------------------------------
# Bulk approve pending users
# ---------------------------------------------------------------------------


class BulkApproveBody(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class _BulkApproveResult(BaseModel):
    user_id: uuid.UUID
    approved: bool
    reason: str | None = None  # error reason when approved=False


class BulkApproveResponse(BaseModel):
    approved_count: int
    results: list[_BulkApproveResult]


@router.post("/approvals/bulk-approve", response_model=BulkApproveResponse)
async def bulk_approve(
    body: BulkApproveBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve a batch of pending users in one request.

    Per-user errors (not found, not in pending_approval state) are
    reported in `results` rather than 4xx-ing the whole request — the
    operator usually wants partial success when one row in their
    selection has gone stale. Writes one USER_APPROVE row per
    successful approval; failures do not write rows.

    Approval emails are dispatched best-effort, same as the
    single-approve endpoint. A delivery failure does not roll back the
    DB-side approval.
    """
    rows = await db.execute(
        select(User).where(User.id.in_(body.user_ids))
    )
    by_id = {u.id: u for u in rows.scalars().all()}

    results: list[_BulkApproveResult] = []
    approved_ids: list[uuid.UUID] = []
    for uid in body.user_ids:
        target = by_id.get(uid)
        if target is None:
            results.append(_BulkApproveResult(
                user_id=uid, approved=False, reason="not_found"
            ))
            continue
        if target.account_status != AccountStatus.PENDING_APPROVAL:
            results.append(_BulkApproveResult(
                user_id=uid,
                approved=False,
                reason=f"not_pending (state: {target.account_status})",
            ))
            continue

        target.account_status = AccountStatus.ACTIVE
        await log_admin_action(
            db,
            admin=admin,
            action=AdminAuditAction.USER_APPROVE,
            target_type=AdminAuditTargetType.USER,
            target_id=target.id,
            target_user_id=target.id,
            before={"account_status": "pending_approval"},
            after={"account_status": "active"},
        )
        approved_ids.append(target.id)
        results.append(_BulkApproveResult(user_id=uid, approved=True))

    await db.commit()

    # Best-effort approval emails. Failures are logged, not raised.
    if approved_ids:
        try:
            from sheaf.config import settings as app_settings

            if app_settings.email_backend != "none":
                from sheaf.services.email import send_email
                from sheaf.services.email_templates import (
                    account_approved_email,
                )

                subject, html, text = account_approved_email()
                for uid in approved_ids:
                    target = by_id.get(uid)
                    if target is None:
                        continue
                    try:
                        addr = decrypt_field(target.email, "email")
                        await send_email(addr, subject, html, text)
                    except Exception:
                        logger.exception(
                            "Failed to send approval email to user %s", uid
                        )
        except Exception:
            logger.exception("Approval email batch failed")

    return BulkApproveResponse(
        approved_count=len(approved_ids),
        results=results,
    )

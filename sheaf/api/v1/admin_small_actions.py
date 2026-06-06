"""Admin small-actions batch.

A grab-bag of tightly scoped admin endpoints that don't fit cleanly
in admin.py (which is already the kitchen sink) or admin_emergency.py
(which is reserved for the three break-glass operations: reset-safety,
bypass-pending, import-log view). Splitting these out keeps the surface
area surveyable.

PR 3 endpoints:

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
        grain-consistent so operators can still filter by user.

PR 4 endpoints:

  - POST /admin/users/{id}/suspend
        Soft-ban an account for an optional duration. Sets
        account_status=SUSPENDED + suspended_until + suspended_reason
        and revokes all sessions atomically. Reason required; logged.
        Duration omitted = indefinite (manual unsuspend to lift).

  - POST /admin/users/{id}/unsuspend
        Lift a soft-ban early. Reason required; logged with
        admin_user_id set. The background sweep also calls into the
        same path with admin=None when an expiry fires.

  - POST /admin/users/{id}/dossier
        GDPR Article 15 metadata bundle. Streams a JSON file with
        everything Sheaf holds about the account: identity, system,
        counts, API key metadata, sessions, client settings, email
        delivery state, admin audit history, import/export jobs.
        Distinct from /v1/export (Article 20 portability). Reason
        required; logged.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

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


# ---------------------------------------------------------------------------
# Suspend / unsuspend
# ---------------------------------------------------------------------------


class SuspendBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    # Optional integer days; omitted = indefinite suspension that
    # only an unsuspend call can lift. Capped at 5 years so an
    # operator can't fat-finger a near-forever ban on a soft route
    # that's meant to auto-restore; for genuinely permanent cases
    # use the BANNED state (which has no auto-restore by design).
    duration_days: int | None = Field(default=None, ge=1, le=1825)


@router.post("/users/{user_id}/suspend")
async def suspend_user(
    user_id: uuid.UUID,
    body: SuspendBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-ban an account.

    Sets `account_status=SUSPENDED`, stamps `suspended_until` from the
    optional duration, captures the reason, and revokes all of the
    user's Redis sessions so they're locked out instantly. The auth
    dep + login endpoint both refuse SUSPENDED users with a detail
    string that includes the reason and expiry, so the user knows
    what happened at next login.

    If the user is already SUSPENDED, this re-stamps with the new
    expiry / reason and writes a new audit row. Useful for extending
    a ban after new evidence.
    """
    from sheaf.services.suspend import apply_suspend

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if target.is_admin:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot suspend an admin account",
        )

    until: datetime | None = None
    if body.duration_days is not None:
        until = datetime.now(UTC) + timedelta(days=body.duration_days)

    before = await apply_suspend(
        db, target, until=until, reason=body.reason,
    )
    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_SUSPEND,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        before=before,
        after={
            "account_status": "suspended",
            "suspended_until": until.isoformat() if until else None,
            "suspended_reason": body.reason,
        },
    )
    await db.commit()
    return {
        "suspended": True,
        "suspended_until": until.isoformat() if until else None,
        "sessions_revoked": before.get("_sessions_revoked"),
    }


@router.post("/users/{user_id}/unsuspend")
async def unsuspend_user(
    user_id: uuid.UUID,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Lift a soft-ban early.

    Idempotent: if the user is already ACTIVE, returns ok=True with
    no state change and writes no audit row (the `before == after`
    case the audit log already skips for routine updates). Use this
    for the "actually they're fine, lift it now" support path.
    """
    from sheaf.services.suspend import apply_unsuspend

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if target.account_status != AccountStatus.SUSPENDED:
        return {"unsuspended": False, "reason": "not_suspended"}

    before = await apply_unsuspend(db, target)
    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_UNSUSPEND,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        before=before,
        after={"account_status": "active"},
    )
    await db.commit()
    return {"unsuspended": True}


# ---------------------------------------------------------------------------
# Dossier export (GDPR Article 15 metadata bundle)
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/dossier")
async def export_user_dossier(
    user_id: uuid.UUID,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """GDPR Article 15 (right of access) metadata bundle for a target
    account, returned as a JSON file download.

    Distinct from `/v1/export`, which is Article 20 (data portability)
    and ships member / journal / front / message *content* in an
    import-friendly shape. This endpoint ships *metadata*: identity,
    flags, counts, structural state, admin trail. For DSAR cases where
    the user can't request portability themselves (locked-out account,
    deceased user with next-of-kin asking).

    Privacy-sensitive read; reason required; writes a
    USER_DOSSIER_EXPORT audit row before the response goes out so an
    aborted-mid-stream request still leaves a trail.

    Deliberately does NOT include: member bios / journal entries /
    message bodies / front history with content / decrypted blobs.
    Those are the user's own data, served only by their own
    portability export — admins don't get a backdoor to it via DSAR.
    """
    from fastapi.responses import JSONResponse

    from sheaf.models.client_settings import ClientSettings
    from sheaf.models.custom_field import CustomFieldDefinition
    from sheaf.models.export_job import ExportJob
    from sheaf.models.front import Front
    from sheaf.models.group import Group
    from sheaf.models.import_job import ImportJob
    from sheaf.models.journal_entry import JournalEntry
    from sheaf.models.message import Message
    from sheaf.models.poll import Poll
    from sheaf.models.reminder import Reminder
    from sheaf.models.tag import Tag
    from sheaf.models.trusted_device import TrustedDevice
    from sheaf.models.uploaded_file import UploadedFile
    from sheaf.models.watch_token import WatchToken

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

    sys_row = await db.execute(
        select(System).where(System.user_id == user_id)
    )
    system = sys_row.scalar_one_or_none()
    system_block: dict[str, object] | None = None
    counts: dict[str, int] = {}
    if system is not None:
        # Count helpers via subqueries to avoid loading content rows.
        async def _count(model, predicate) -> int:
            row = await db.execute(select(func.count(model.id)).where(predicate))
            return int(row.scalar_one())

        counts = {
            "members": await _count(Member, Member.system_id == system.id),
            "fronts": await _count(Front, Front.system_id == system.id),
            "groups": await _count(Group, Group.system_id == system.id),
            "tags": await _count(Tag, Tag.system_id == system.id),
            "custom_fields": await _count(
                CustomFieldDefinition,
                CustomFieldDefinition.system_id == system.id,
            ),
            "journal_entries": await _count(
                JournalEntry, JournalEntry.system_id == system.id,
            ),
            "messages": await _count(Message, Message.system_id == system.id),
            "polls": await _count(Poll, Poll.system_id == system.id),
            "reminders": await _count(
                Reminder, Reminder.system_id == system.id,
            ),
            "watch_tokens": await _count(
                WatchToken, WatchToken.system_id == system.id,
            ),
            "uploaded_files": await _count(
                UploadedFile, UploadedFile.user_id == user_id,
            ),
        }
        system_block = {
            "id": str(system.id),
            "name": system.name or "",
            "created_at": system.created_at.isoformat(),
            "delete_confirmation": str(system.delete_confirmation.value),
            "safety_grace_period_days": system.safety_grace_period_days,
            "safety_applies_to_members": system.safety_applies_to_members,
            "safety_applies_to_groups": system.safety_applies_to_groups,
            "safety_applies_to_tags": system.safety_applies_to_tags,
            "safety_applies_to_fields": system.safety_applies_to_fields,
            "safety_applies_to_fronts": system.safety_applies_to_fronts,
            "safety_applies_to_journals": system.safety_applies_to_journals,
            "safety_applies_to_images": system.safety_applies_to_images,
            "safety_applies_to_revisions": system.safety_applies_to_revisions,
            "safety_applies_to_notifications": system.safety_applies_to_notifications,
            "safety_applies_to_reminders": system.safety_applies_to_reminders,
            "safety_applies_to_polls": system.safety_applies_to_polls,
            "safety_applies_to_messages": system.safety_applies_to_messages,
        }

    # API key metadata only — never the hashed key value or anything
    # that could rebuild the plaintext.
    api_key_rows = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user_id)
    )
    api_keys = [
        {
            "id": str(k.id),
            "name": k.name,
            "scopes": list(k.scopes or []),
            "created_at": k.created_at.isoformat(),
            "last_used_at": (
                k.last_used_at.isoformat() if k.last_used_at else None
            ),
            "expires_at": (
                k.expires_at.isoformat() if k.expires_at else None
            ),
        }
        for k in api_key_rows.scalars().all()
    ]

    sessions = await list_user_sessions(user_id)
    session_block = [
        {
            "id": s.get("id"),
            "user_agent": s.get("user_agent"),
            "ip": s.get("ip"),
            "created_at": s.get("created_at"),
            "last_seen_at": s.get("last_seen_at"),
            "nickname": s.get("nickname"),
        }
        for s in sessions
    ]

    trusted_rows = await db.execute(
        select(TrustedDevice).where(TrustedDevice.user_id == user_id)
    )
    trusted_block = [
        {
            "id": str(t.id),
            "nickname": t.nickname,
            "client_name": t.client_name,
            "user_agent": t.user_agent,
            "created_at": t.created_at.isoformat(),
            "created_ip": t.created_ip,
            "last_used_at": (
                t.last_used_at.isoformat() if t.last_used_at else None
            ),
            "last_used_ip": t.last_used_ip,
            "expires_at": t.expires_at.isoformat(),
        }
        for t in trusted_rows.scalars().all()
    ]

    client_settings_rows = await db.execute(
        select(ClientSettings).where(ClientSettings.user_id == user_id)
    )
    client_settings_block = [
        {"client_id": getattr(cs, "client_id", None), "settings": cs.settings}
        for cs in client_settings_rows.scalars().all()
    ]

    audit_rows = await db.execute(
        select(AdminAuditEvent)
        .where(AdminAuditEvent.target_user_id == user_id)
        .order_by(desc(AdminAuditEvent.created_at))
        .limit(500)
    )
    audit_block = [
        {
            "id": str(r.id),
            "action": str(r.action),
            "target_type": str(r.target_type),
            "target_id": str(r.target_id) if r.target_id else None,
            "admin_email": r.admin_email,
            "reason": r.reason,
            "before_json": r.before_json,
            "after_json": r.after_json,
            "created_at": r.created_at.isoformat(),
        }
        for r in audit_rows.scalars().all()
    ]

    import_rows = await db.execute(
        select(ImportJob)
        .where(ImportJob.user_id == user_id)
        .order_by(desc(ImportJob.created_at))
        .limit(100)
    )
    import_block = [
        {
            "id": str(j.id),
            "source": str(j.source),
            "status": str(j.status),
            "created_at": j.created_at.isoformat(),
            "finished_at": (
                j.finished_at.isoformat() if j.finished_at else None
            ),
            "counts": j.counts or {},
        }
        for j in import_rows.scalars().all()
    ]

    export_rows = await db.execute(
        select(ExportJob)
        .where(ExportJob.user_id == user_id)
        .order_by(desc(ExportJob.requested_at))
        .limit(100)
    )
    export_block = [
        {
            "id": str(j.id),
            "status": str(j.status),
            "requested_at": j.requested_at.isoformat(),
            "completed_at": (
                j.completed_at.isoformat() if j.completed_at else None
            ),
            "file_size_bytes": j.file_size_bytes,
        }
        for j in export_rows.scalars().all()
    ]

    dossier = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_by_admin_id": str(admin.id),
        "reason": body.reason,
        "user": {
            "id": str(target.id),
            "email": email,
            "tier": str(target.tier.value),
            "is_admin": target.is_admin,
            "account_status": str(target.account_status),
            "email_verified": target.email_verified,
            "totp_enabled": target.totp_enabled,
            "signup_ip": target.signup_ip,
            "member_limit": target.member_limit,
            "can_upload_images": target.can_upload_images,
            "can_upload_animated_images": target.can_upload_animated_images,
            "created_at": target.created_at.isoformat(),
            "last_login_at": (
                target.last_login_at.isoformat()
                if target.last_login_at
                else None
            ),
            "suspended_until": (
                target.suspended_until.isoformat()
                if target.suspended_until
                else None
            ),
            "suspended_reason": target.suspended_reason,
            "email_delivery_status": str(target.email_delivery_status),
            "email_revalidation_required": target.email_revalidation_required,
        },
        "system": system_block,
        "counts": counts,
        "api_keys": api_keys,
        "active_sessions": session_block,
        "trusted_devices": trusted_block,
        "client_settings": client_settings_block,
        "admin_audit_history": audit_block,
        "import_jobs": import_block,
        "export_jobs": export_block,
    }

    # Log BEFORE returning so a connection drop mid-stream still
    # leaves a trail. The actor's intent to view was complete by
    # this point.
    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_DOSSIER_EXPORT,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        before=None,
        after={
            "section_counts": {
                k: len(v) if isinstance(v, list) else (1 if v else 0)
                for k, v in dossier.items()
                if k in {
                    "api_keys",
                    "active_sessions",
                    "trusted_devices",
                    "admin_audit_history",
                    "import_jobs",
                    "export_jobs",
                    "client_settings",
                }
            },
        },
    )
    await db.commit()

    filename = f"sheaf-dossier-{user_id}-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    return JSONResponse(
        content=dossier,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )

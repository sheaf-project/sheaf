"""Account-level endpoints — distinct from /auth (which handles
authentication mechanics) and /export (Article 20 portable data).

Currently hosts the Article 15 "right of access" endpoint: everything
the service holds *about* the user account, including server-derived
telemetry that doesn't belong in a portable export.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.auth.lockout import ensure_not_locked, record_login_failure
from sheaf.auth.passwords import verify_password
from sheaf.auth.sessions import list_user_sessions
from sheaf.auth.totp import TotpCheck, check_code_once, totp_error_detail
from sheaf.crypto import blind_index, decrypt
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.activity_event import ActivityEvent
from sheaf.models.api_key import ApiKey
from sheaf.models.client_settings import ClientSettings
from sheaf.models.email_suppression import EmailSuppression
from sheaf.models.notification_channel import NotificationChannel
from sheaf.models.pending_action import PendingAction, PendingActionStatus
from sheaf.models.retention_trim_notice import RetentionTrimNotice
from sheaf.models.safety_change_request import (
    SafetyChangeRequest,
    SafetyChangeStatus,
)
from sheaf.models.system import System
from sheaf.models.trusted_device import TrustedDevice
from sheaf.models.user import User
from sheaf.models.watch_token import WatchToken
from sheaf.schemas.activity import ActivityEventRead
from sheaf.services.security_events import events_for_user

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/activity", response_model=list[ActivityEventRead])
async def list_account_activity(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ActivityEvent]:
    """The caller's own account activity log, newest first.

    Self-only (rows where `user_id == self.id`); no admin gate. This is the
    transparency surface for consequential and automated actions on the
    account, distinct from the admin-activity view (admin actions) and the
    operator-facing security-event log.
    """
    stmt = (
        select(ActivityEvent)
        .where(ActivityEvent.user_id == user.id)
        .order_by(desc(ActivityEvent.created_at), desc(ActivityEvent.id))
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


class AccountDataRequest(BaseModel):
    password: str
    totp_code: str | None = None


@router.post("/data", dependencies=[rate_limit(5, 60, "user", fail_closed=True)])
async def get_account_data(
    body: AccountDataRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Article 15 (right of access) — everything Sheaf holds about the
    requesting user account.

    Distinct from `/v1/export`, which is Article 20 (data portability) and
    only includes plural-system content. This endpoint adds account
    identity, sessions, IPs, API key audit metadata, email delivery
    state, the security event log (auth attempts with IPs), and other
    server-derived data that should NEVER ride along with a portable
    export (info-leak hazard if shared or imported elsewhere).

    Always requires password (and TOTP if enrolled), regardless of the
    system's `delete_confirmation` setting — this is the highest-value
    read endpoint for an attacker with a hijacked session, so we don't
    let users opt out of the gate. Method is POST because it carries
    credentials in the body; semantically it's a read.

    Excluded by design (would defeat their security purpose):
    - Password hash
    - TOTP secret
    - Recovery code hashes
    - API key plaintext or hash
    - Session tokens (only metadata)
    - Trusted device tokens (only metadata)
    """
    # Auth method check: refuse API-key access. The data here is too
    # sensitive to expose via a programmatic credential — must be a
    # session/JWT-authenticated request from the user themselves.
    if getattr(request.state, "auth_method", None) == "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "API keys cannot access account data. Sign in with a "
                "session or JWT to download your account data."
            ),
        )

    # Step-up: always password, plus TOTP if enrolled. Independent of
    # System Safety's delete_confirmation tier — that gates deletes; this
    # gates the highest-value read.
    ensure_not_locked(user)

    if not await verify_password(body.password, user.password_hash):
        # 403: step-up auth denial. See system_safety.verify_destructive_auth
        # for full reasoning.
        await record_login_failure(db, user)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password incorrect",
        )
    if user.totp_enabled:
        if not body.totp_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TOTP code required",
            )
        secret = decrypt(user.totp_secret)
        totp_result = await check_code_once(user.id, secret, body.totp_code)
        if totp_result is not TotpCheck.OK:
            await record_login_failure(db, user, reason="totp_failures")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=totp_error_detail(totp_result),
            )

    sessions_raw = await list_user_sessions(user.id)

    api_keys_result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id)
    )
    api_keys = api_keys_result.scalars().all()

    trusted_devices_result = await db.execute(
        select(TrustedDevice).where(TrustedDevice.user_id == user.id)
    )
    trusted_devices = trusted_devices_result.scalars().all()

    # Security event log for this account. Bounded by the retention
    # window, but a stuffing victim can accumulate many failed-login
    # rows, so cap at a generous newest-first slice rather than risk a
    # huge response; the cap is surfaced in the payload.
    _SECURITY_EVENT_CAP = 2000
    security_events = await events_for_user(
        db, user.id, limit=_SECURITY_EVENT_CAP
    )

    client_settings_result = await db.execute(
        select(ClientSettings).where(ClientSettings.user_id == user.id)
    )
    client_settings = client_settings_result.scalars().all()

    # User.email is application-encrypted at rest; decrypt to plaintext
    # for both the suppression blind-index lookup AND the response body
    # (the user requesting their own data obviously already knows their
    # email, but dumping ciphertext would be misleading + useless).
    plaintext_email = decrypt(user.email)

    email_suppression_result = await db.execute(
        select(EmailSuppression).where(
            EmailSuppression.address_hash == blind_index(plaintext_email)
        )
    )
    email_suppression = email_suppression_result.scalar_one_or_none()

    # Pending System Safety actions awaiting their grace period.
    system_result = await db.execute(
        select(System).where(System.user_id == user.id)
    )
    system = system_result.scalar_one_or_none()

    pending_actions: list = []
    pending_changes: list = []
    if system is not None:
        actions_result = await db.execute(
            select(PendingAction)
            .where(
                PendingAction.system_id == system.id,
                PendingAction.status == PendingActionStatus.PENDING,
            )
            .order_by(PendingAction.requested_at.desc())
        )
        pending_actions = list(actions_result.scalars().all())

        changes_result = await db.execute(
            select(SafetyChangeRequest)
            .where(
                SafetyChangeRequest.system_id == system.id,
                SafetyChangeRequest.status == SafetyChangeStatus.PENDING,
            )
            .order_by(SafetyChangeRequest.requested_at.desc())
        )
        pending_changes = list(changes_result.scalars().all())

    # Notification channels this user is the recipient of (across any
    # system). Owner-side channels live with their system data.
    receiving_result = await db.execute(
        select(NotificationChannel, WatchToken, System)
        .join(WatchToken, NotificationChannel.watch_token_id == WatchToken.id)
        .join(System, WatchToken.system_id == System.id)
        .where(NotificationChannel.redeemed_by_account_id == user.id)
        .order_by(NotificationChannel.redeemed_at.desc())
    )
    receiving_rows = receiving_result.all()

    # Retention trim notices issued against this user (e.g. tier downgrade
    # that pruned data). Rare, but transparency-appropriate.
    trim_result = await db.execute(
        select(RetentionTrimNotice)
        .where(RetentionTrimNotice.user_id == user.id)
        .order_by(RetentionTrimNotice.requested_at.desc())
    )
    trim_notices = list(trim_result.scalars().all())

    return {
        "version": "1",
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": (
            "GDPR Article 15 right of access — everything Sheaf holds "
            "about your account. Distinct from /v1/export which covers "
            "Article 20 data portability."
        ),
        "account": {
            "id": str(user.id),
            "email": plaintext_email,
            "tier": user.tier.value,
            "is_admin": user.is_admin,
            "account_status": user.account_status.value,
            "email_verified": user.email_verified,
            "email_verification_sent_at": _iso(user.email_verification_sent_at),
            "totp_enabled": user.totp_enabled,
            "can_upload_images": user.can_upload_images,
            "member_limit": user.member_limit,
            "signup_ip": user.signup_ip,
            "registered_at": _iso(user.created_at),
            "last_login_at": _iso(user.last_login_at),
            "failed_login_count": user.failed_login_count,
            "locked_until": _iso(user.locked_until),
            "deletion_requested_at": _iso(user.deletion_requested_at),
            "deletion_reminders_sent": user.deletion_reminders_sent,
            "newsletter_opt_in": user.newsletter_opt_in,
            "newsletter_opted_in_at": _iso(user.newsletter_opted_in_at),
            "email_delivery_status": user.email_delivery_status.value,
            "email_delivery_status_changed_at": _iso(
                user.email_delivery_status_changed_at
            ),
            "email_soft_bounce_count": user.email_soft_bounce_count,
            "email_revalidation_required": user.email_revalidation_required,
            "disable_cdn_during_ddos": user.disable_cdn_during_ddos,
        },
        "sessions": [
            {
                "id": s.get("id"),
                "ip": s.get("ip"),
                "user_agent": s.get("user_agent"),
                "created_at": s.get("created_at"),
                "last_active_at": s.get("last_active_at"),
                "parent_session_id": s.get("parent_session_id"),
            }
            for s in sessions_raw
        ],
        "trusted_devices": [
            {
                "id": str(d.id),
                "nickname": d.nickname,
                "user_agent": d.user_agent,
                "created_ip": d.created_ip,
                "last_used_at": _iso(d.last_used_at),
                "last_used_ip": d.last_used_ip,
                "created_at": _iso(d.created_at),
                "expires_at": _iso(d.expires_at),
            }
            for d in trusted_devices
        ],
        "api_keys": [
            {
                "id": str(k.id),
                "name": k.name,
                "scopes": k.scopes,
                "created_at": _iso(k.created_at),
                "last_used_at": _iso(k.last_used_at),
                "expires_at": _iso(k.expires_at),
            }
            for k in api_keys
        ],
        # Auth-funnel events tied to this account (logins, registration,
        # password resets/changes), each with its originating IP. This is
        # the user-facing half of the security event log promised in the
        # privacy policy; the full cross-account search is admin-only.
        "security_events_truncated": len(security_events) >= _SECURITY_EVENT_CAP,
        "security_events": [
            {
                "created_at": _iso(e.created_at),
                "event_type": str(e.event_type),
                "outcome": e.outcome,
                "ip": e.ip,
                "user_agent": e.user_agent,
            }
            for e in security_events
        ],
        "email_suppression": (
            {
                "reason": email_suppression.reason,
                "suppressed_at": _iso(email_suppression.suppressed_at),
                "expires_at": _iso(email_suppression.expires_at),
            }
            if email_suppression
            else None
        ),
        "client_settings": [
            {
                "client_id": cs.client_id,
                "settings": cs.settings,
                "created_at": _iso(cs.created_at),
                "updated_at": _iso(cs.updated_at),
            }
            for cs in client_settings
        ],
        "pending_safety_actions": [
            {
                "id": str(a.id),
                "action_type": a.action_type,
                "target_id": str(a.target_id),
                "target_label": a.target_label,
                "requested_at": _iso(a.requested_at),
                "finalize_after": _iso(a.finalize_after),
                "status": a.status,
            }
            for a in pending_actions
        ],
        "pending_safety_changes": [
            {
                "id": str(c.id),
                "changes": c.changes,
                "requested_at": _iso(c.requested_at),
                "finalize_after": _iso(c.finalize_after),
                "status": c.status,
            }
            for c in pending_changes
        ],
        "receiving_notification_channels": [
            {
                "channel_id": str(channel.id),
                "channel_name": channel.name,
                "system_id": str(system_row.id),
                "system_name": system_row.name,
                "destination_type": channel.destination_type,
                "destination_state": channel.destination_state,
                "redeemed_at": _iso(channel.redeemed_at),
                "last_delivered_at": _iso(channel.last_delivered_at),
            }
            for channel, _token, system_row in receiving_rows
        ],
        "retention_trim_notices": [
            {
                "id": str(n.id),
                "from_tier": n.from_tier,
                "to_tier": n.to_tier,
                "reason": n.reason,
                "status": n.status,
                "requested_at": _iso(n.requested_at),
                "effective_at": _iso(n.effective_at),
                "cancelled_at": _iso(n.cancelled_at),
                "completed_at": _iso(n.completed_at),
            }
            for n in trim_notices
        ],
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None

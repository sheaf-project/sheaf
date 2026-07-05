import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.api.v1.admin_small_actions import AdminReasonBody
from sheaf.auth.dependencies import get_admin_user, get_admin_write_user, get_current_user
from sheaf.auth.lockout import ensure_not_locked, record_login_failure
from sheaf.auth.sessions import check_admin_step_up, set_admin_step_up
from sheaf.auth.totp import TotpCheck, check_code_once, totp_error_detail
from sheaf.config import settings
from sheaf.crypto import decrypt_field
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.admin_audit_event import AdminAuditAction, AdminAuditTargetType
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import AccountStatus, User, UserTier
from sheaf.services.admin_audit import log_admin_action
from sheaf.services.file_cleanup import cleanup_orphaned_files
from sheaf.services.front_retention import sweep_front_retention

logger = logging.getLogger("sheaf.admin")

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Admin step-up auth
# ---------------------------------------------------------------------------

class AdminStepUpVerify(BaseModel):
    password: str | None = None
    totp_code: str | None = None


@router.get("/auth")
async def get_admin_auth_status(
    request: Request,
    user: User = Depends(get_current_user),
):
    """Return the configured step-up level and whether this user has completed it."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    level = settings.admin_auth_level
    auth_method = getattr(request.state, "auth_method", None)

    if auth_method == "api_key" or level == "none":
        verified = True
    else:
        verified = await check_admin_step_up(
            user.id, getattr(request.state, "session_id", None)
        )

    return {
        "level": level,
        "verified": verified,
        "totp_enabled": user.totp_enabled,
    }


@router.post("/auth", dependencies=[rate_limit(5, 60, "user")])
async def verify_admin_step_up(
    body: AdminStepUpVerify,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Complete admin step-up authentication for the calling session."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    # Step-up is granted per-session, so it needs a session to bind to.
    # API-key auth is exempt from step-up entirely and never lands here.
    session_id = getattr(request.state, "session_id", None)
    if session_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Step-up requires a session-bound credential",
        )

    level = settings.admin_auth_level

    # Step-up verifies brute-forceable credentials, so it feeds the same
    # unified lockout state as login/TOTP — otherwise a hijacked admin
    # session could brute the password here without ever tripping it.
    if level in ("password", "totp"):
        ensure_not_locked(user)

    if level == "password":
        if not body.password:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Password required"
            )
        from sheaf.auth.passwords import verify_password
        if not await verify_password(body.password, user.password_hash):
            await record_login_failure(db, user)
            # 403: caller is already authenticated; this step-up gate
            # denies the action. 401 would falsely trigger the frontend's
            # silent-refresh-and-retry path.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Incorrect password"
            )

    elif level == "totp":
        if not user.totp_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="TOTP must be enabled on your account to access the admin dashboard",
            )
        if not body.totp_code:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="TOTP code required"
            )
        totp_secret = decrypt_field(user.totp_secret, "totp_secret")
        totp_result = await check_code_once(user.id, totp_secret, body.totp_code)
        if totp_result is not TotpCheck.OK:
            await record_login_failure(db, user, reason="totp_failures")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=totp_error_detail(totp_result),
            )

    await set_admin_step_up(user.id, session_id)
    return {"verified": True}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_stats(
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate system stats. Requires admin:read scope or is_admin."""
    total_users = await db.scalar(select(func.count(User.id)))
    total_members = await db.scalar(select(func.count(Member.id)))
    total_storage = await db.scalar(
        select(func.coalesce(func.sum(UploadedFile.size_bytes), 0))
    )

    # Users by tier
    rows = await db.execute(
        select(User.tier, func.count(User.id)).group_by(User.tier)
    )
    users_by_tier = {row.tier: row.count for row in rows}

    return {
        "total_users": total_users,
        "total_members": total_members,
        "total_storage_bytes": total_storage,
        "users_by_tier": users_by_tier,
    }


@router.get("/pushover-usage")
async def get_pushover_usage(_: User = Depends(get_admin_user)):
    """Current month's shared-app Pushover delivery count vs the configured
    monthly cap. Channels with a BYO destination_config.app_token aren't
    counted here — they hit the recipient's own Pushover quota."""
    from datetime import UTC, datetime

    from sheaf.services.notifications.pushover_counter import (
        get_monthly_count,
    )

    count = await get_monthly_count()
    cap = settings.pushover_max_per_month
    return {
        "month": datetime.now(UTC).strftime("%Y-%m"),
        "count": count,
        "cap": cap,
        # Convenience: when cap=0 we don't enforce, surface that explicitly.
        "enforced": cap > 0,
    }


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

class AdminUserRead(BaseModel):
    id: str
    email: str
    tier: str
    is_admin: bool
    account_status: str
    email_verified: bool
    totp_enabled: bool
    signup_ip: str | None
    member_limit: int | None
    storage_used_bytes: int
    member_count: int
    can_upload_images: bool
    can_upload_animated_images: bool
    created_at: datetime
    last_login_at: datetime | None
    suspended_until: datetime | None
    suspended_reason: str | None


class AdminUserUpdate(BaseModel):
    tier: UserTier | None = None
    is_admin: bool | None = None
    member_limit: int | None = None
    clear_member_limit: bool = False  # set True to reset to tier default (null)
    can_upload_images: bool | None = None
    can_upload_animated_images: bool | None = None


@router.get("/users", response_model=list[AdminUserRead])
async def list_users(
    search: str = "",
    signup_ip: str = "",
    page: int = 1,
    limit: int = 50,
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users with member counts. Requires admin:read scope or is_admin.

    `signup_ip` filters to users whose recorded signup IP matches exactly.
    Useful for abuse triage when one IP shows up across multiple complaints.
    Exact-match only — partial / CIDR matching is intentionally absent so
    operators don't accidentally surface broad swaths of accounts that just
    happened to be behind the same NAT."""
    member_count_sq = (
        select(System.user_id, func.count(Member.id).label("member_count"))
        .outerjoin(Member, Member.system_id == System.id)
        .group_by(System.user_id)
        .subquery()
    )

    storage_sq = (
        select(
            UploadedFile.user_id,
            func.coalesce(func.sum(UploadedFile.size_bytes), 0).label("storage_used_bytes"),
        )
        .group_by(UploadedFile.user_id)
        .subquery()
    )

    query = (
        select(
            User,
            func.coalesce(member_count_sq.c.member_count, 0).label("member_count"),
            func.coalesce(storage_sq.c.storage_used_bytes, 0).label("storage_used_bytes"),
        )
        .outerjoin(member_count_sq, member_count_sq.c.user_id == User.id)
        .outerjoin(storage_sq, storage_sq.c.user_id == User.id)
        .order_by(User.created_at.desc())
    )

    if signup_ip:
        query = query.where(User.signup_ip == signup_ip)

    offset = max(0, (page - 1) * limit)

    def _decrypt_email(user: User) -> str:
        try:
            return decrypt_field(user.email, "email")
        except Exception:
            return "<encrypted>"

    if search:
        # Email is encrypted with no substring index, so a search has to
        # decrypt and filter in Python. Admin-only and lower-frequency, so
        # the full scan is acceptable here.
        rows = await db.execute(query)
        needle = search.lower()
        matched = []
        for user, member_count, storage in rows:
            email = _decrypt_email(user)
            if needle in email.lower():
                matched.append((user, member_count, storage, email))
        page_rows = matched[offset : offset + limit]
    else:
        # Unfiltered list: paginate in SQL so only the current page is
        # loaded and decrypted, not the entire users table.
        rows = await db.execute(query.offset(offset).limit(limit))
        page_rows = [
            (user, member_count, storage, _decrypt_email(user))
            for user, member_count, storage in rows
        ]

    return [
        AdminUserRead(
            id=str(user.id),
            email=email,
            tier=user.tier,
            is_admin=user.is_admin,
            account_status=user.account_status,
            email_verified=user.email_verified,
            totp_enabled=user.totp_enabled,
            signup_ip=user.signup_ip,
            member_limit=user.member_limit,
            storage_used_bytes=storage_used_bytes,
            member_count=member_count,
            can_upload_images=user.can_upload_images,
            can_upload_animated_images=user.can_upload_animated_images,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            suspended_until=user.suspended_until,
            suspended_reason=user.suspended_reason,
        )
        for user, member_count, storage_used_bytes, email in page_rows
    ]


@router.patch("/users/{user_id}", response_model=AdminUserRead)
async def update_user(
    user_id: uuid.UUID,
    body: AdminUserUpdate,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a user's tier, is_admin, or member limit. Requires admin:write scope or is_admin."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Snapshot only the fields the audit log cares about, before any
    # mutation. The diff is computed at write time so unchanged keys
    # don't pollute the row.
    before_snapshot: dict[str, object] = {
        "tier": str(target.tier.value),
        "is_admin": target.is_admin,
        "can_upload_images": target.can_upload_images,
        "can_upload_animated_images": target.can_upload_animated_images,
        "member_limit": target.member_limit,
    }

    if body.tier is not None:
        target.tier = body.tier
    if body.is_admin is not None:
        target.is_admin = body.is_admin
    if body.can_upload_images is not None:
        target.can_upload_images = body.can_upload_images
    if body.can_upload_animated_images is not None:
        target.can_upload_animated_images = body.can_upload_animated_images
    if body.clear_member_limit:
        target.member_limit = None
    elif body.member_limit is not None:
        target.member_limit = body.member_limit

    after_snapshot: dict[str, object] = {
        "tier": str(target.tier.value),
        "is_admin": target.is_admin,
        "can_upload_images": target.can_upload_images,
        "can_upload_animated_images": target.can_upload_animated_images,
        "member_limit": target.member_limit,
    }
    changed = {
        k for k in before_snapshot if before_snapshot[k] != after_snapshot[k]
    }
    diff_before = {k: before_snapshot[k] for k in changed}
    diff_after = {k: after_snapshot[k] for k in changed}
    if changed:
        await log_admin_action(
            db,
            admin=admin,
            action=AdminAuditAction.USER_UPDATE,
            target_type=AdminAuditTargetType.USER,
            target_id=target.id,
            target_user_id=target.id,
            before=diff_before,
            after=diff_after,
        )

    # Get member count and storage for response
    member_count = await db.scalar(
        select(func.count(Member.id))
        .join(System, System.id == Member.system_id)
        .where(System.user_id == user_id)
    ) or 0

    storage_used = await db.scalar(
        select(func.coalesce(func.sum(UploadedFile.size_bytes), 0))
        .where(UploadedFile.user_id == user_id)
    )

    try:
        email = decrypt_field(target.email, "email")
    except Exception:
        email = "<encrypted>"

    await db.commit()

    return AdminUserRead(
        id=str(target.id),
        email=email,
        tier=target.tier,
        is_admin=target.is_admin,
        account_status=target.account_status,
        email_verified=target.email_verified,
        totp_enabled=target.totp_enabled,
        signup_ip=target.signup_ip,
        member_limit=target.member_limit,
        storage_used_bytes=storage_used,
        member_count=member_count,
        can_upload_images=target.can_upload_images,
        can_upload_animated_images=target.can_upload_animated_images,
        created_at=target.created_at,
        last_login_at=target.last_login_at,
        suspended_until=target.suspended_until,
        suspended_reason=target.suspended_reason,
    )


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

class PendingUserRead(BaseModel):
    id: str
    email: str
    email_verified: bool
    signup_ip: str | None
    created_at: datetime


@router.get("/approvals", response_model=list[PendingUserRead])
async def list_pending_approvals(
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users with pending_approval status."""
    result = await db.execute(
        select(User)
        .where(User.account_status == AccountStatus.PENDING_APPROVAL)
        .order_by(User.created_at.asc())
    )
    users = []
    for user in result.scalars():
        try:
            email = decrypt_field(user.email, "email")
        except Exception:
            email = "<encrypted>"
        users.append(PendingUserRead(
            id=str(user.id),
            email=email,
            email_verified=user.email_verified,
            signup_ip=user.signup_ip,
            created_at=user.created_at,
        ))
    return users


@router.post("/users/{user_id}/approve")
async def approve_user(
    user_id: uuid.UUID,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve a pending user account."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.account_status != AccountStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User is not pending approval (status: {target.account_status})",
        )

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
    await db.commit()

    # Send approval notification email if configured
    try:
        email = decrypt_field(target.email, "email")
        from sheaf.config import settings as app_settings

        if app_settings.email_backend != "none":
            from sheaf.services.email import send_email
            from sheaf.services.email_templates import account_approved_email

            subject, html, text = account_approved_email()
            await send_email(email, subject, html, text)
    except Exception:
        logger.exception("Failed to send approval email to user %s", user_id)

    return {"approved": True}


@router.post("/users/{user_id}/reject")
async def reject_user(
    user_id: uuid.UUID,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Reject a pending user account. Deletes the user and their system."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.account_status != AccountStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User is not pending approval (status: {target.account_status})",
        )

    # Audit-log BEFORE the cascade delete clears the target user
    # row out from under us. target_user_id is captured here; the
    # row will SET NULL once the user is deleted, but the audit
    # row remains attributable via admin_email + reason.
    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_REJECT,
        target_type=AdminAuditTargetType.USER,
        target_id=target.id,
        target_user_id=target.id,
        before={"account_status": "pending_approval"},
        after={"account_status": "deleted"},
    )

    # Send rejection notification email if configured
    try:
        email = decrypt_field(target.email, "email")
        from sheaf.config import settings as app_settings

        if app_settings.email_backend != "none":
            from sheaf.services.email import send_email
            from sheaf.services.email_templates import account_rejected_email

            subject, html, text = account_rejected_email()
            await send_email(email, subject, html, text)
    except Exception:
        logger.exception("Failed to send rejection email to user %s", user_id)

    # Delete system (cascade will handle members, fronts, etc.)
    sys_result = await db.execute(select(System).where(System.user_id == user_id))
    system = sys_result.scalar_one_or_none()
    if system:
        await db.delete(system)

    await db.delete(target)
    await db.commit()
    return {"rejected": True}


# ---------------------------------------------------------------------------
# Maintenance operations (kept from before, now using get_admin_write_user)
# ---------------------------------------------------------------------------

@router.post("/retention/run")
async def run_retention(
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger the user-opt-in front-history retention sweep. Admin only."""
    result = await sweep_front_retention(db)
    count = result.get("items_processed", 0)
    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.JOB_TRIGGER,
        target_type=AdminAuditTargetType.JOB,
        after={"job": "retention/run", "pruned": count},
    )
    await db.commit()
    return {"pruned": count}


@router.post("/cleanup/run")
async def run_cleanup(
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Clean up orphaned files for all users. Admin only."""
    result = await db.execute(select(User.id))
    user_ids = [str(uid) for (uid,) in result]

    total_orphaned = 0
    total_freed = 0
    for uid in user_ids:
        stats = await cleanup_orphaned_files(db, uid)
        total_orphaned += stats["orphaned"]
        total_freed += stats["freed_bytes"]

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.JOB_TRIGGER,
        target_type=AdminAuditTargetType.JOB,
        after={
            "job": "cleanup/run",
            "orphaned": total_orphaned,
            "freed_bytes": total_freed,
        },
    )
    await db.commit()

    return {
        "users_checked": len(user_ids),
        "total_orphaned": total_orphaned,
        "total_freed_bytes": total_freed,
    }


@router.get("/storage/stats")
async def get_storage_stats(
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Storage statistics from uploaded_files table. Admin only."""
    result = await db.execute(
        select(
            func.coalesce(func.sum(UploadedFile.size_bytes), 0),
            func.count(UploadedFile.id),
            func.count(func.distinct(UploadedFile.user_id)),
        )
    )
    total_bytes, total_files, users_with_files = result.one()
    return {
        "total_bytes": total_bytes,
        "total_files": total_files,
        "users_with_files": users_with_files,
    }


# Legacy endpoint kept for backwards compatibility
class MemberLimitOverride(BaseModel):
    member_limit: int | None = None


@router.put("/users/{user_id}/member-limit")
async def set_member_limit(
    user_id: uuid.UUID,
    body: MemberLimitOverride,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Set or reset a user's member limit override. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    old_limit = target.member_limit
    target.member_limit = body.member_limit

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_MEMBER_LIMIT_SET,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        before={"member_limit": old_limit},
        after={"member_limit": body.member_limit},
    )
    await db.commit()
    return {"user_id": str(user_id), "member_limit": target.member_limit}


# ---------------------------------------------------------------------------
# Invite codes
# ---------------------------------------------------------------------------

class InviteCodeCreate(BaseModel):
    max_uses: int = 0  # 0 = unlimited
    note: str | None = None
    expires_at: datetime | None = None


@router.get("/invites")
async def list_invites(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all invite codes. Admin only."""
    from sheaf.models.invite_code import InviteCode

    result = await db.execute(
        select(InviteCode).order_by(InviteCode.created_at.desc())
    )
    invites = result.scalars().all()

    # Resolve creator emails
    creator_ids = {i.created_by for i in invites if i.created_by}
    creators: dict[uuid.UUID, str] = {}
    if creator_ids:
        users_result = await db.execute(
            select(User.id, User.email).where(User.id.in_(creator_ids))
        )
        for uid, email_enc in users_result.all():
            try:
                creators[uid] = decrypt_field(email_enc, "email")
            except Exception:
                creators[uid] = "<encrypted>"

    return [
        {
            "id": str(i.id),
            "code": i.code,
            "created_by_email": creators.get(i.created_by) if i.created_by else None,
            "max_uses": i.max_uses,
            "use_count": i.use_count,
            "note": i.note,
            "expires_at": i.expires_at.isoformat() if i.expires_at else None,
            "created_at": i.created_at.isoformat(),
        }
        for i in invites
    ]


@router.post("/invites", status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: InviteCodeCreate,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new invite code. Admin only."""
    import secrets as _secrets

    from sheaf.models.invite_code import InviteCode

    code = _secrets.token_urlsafe(16)
    invite = InviteCode(
        code=code,
        created_by=admin.id,
        max_uses=body.max_uses,
        note=body.note,
        expires_at=body.expires_at,
    )
    db.add(invite)
    await db.flush()

    # Audit the creation; the code itself is a registration credential
    # and stays out of the log.
    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.INVITE_CREATE,
        target_type=AdminAuditTargetType.INVITE,
        target_id=invite.id,
        after={
            "max_uses": invite.max_uses,
            "note": invite.note,
            "expires_at": (
                invite.expires_at.isoformat() if invite.expires_at else None
            ),
        },
    )
    await db.commit()
    await db.refresh(invite)

    admin_email: str | None = None
    try:
        admin_email = decrypt_field(admin.email, "email")
    except Exception:
        admin_email = "<encrypted>"

    return {
        "id": str(invite.id),
        "code": invite.code,
        "created_by_email": admin_email,
        "max_uses": invite.max_uses,
        "use_count": invite.use_count,
        "note": invite.note,
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
        "created_at": invite.created_at.isoformat(),
    }


@router.delete("/invites/{invite_id}")
async def delete_invite(
    invite_id: uuid.UUID,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an invite code. Admin only."""
    from sheaf.models.invite_code import InviteCode

    result = await db.execute(
        select(InviteCode).where(InviteCode.id == invite_id)
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invite code not found"
        )
    snapshot = {
        "note": invite.note,
        "max_uses": invite.max_uses,
        "use_count": invite.use_count,
    }
    await db.delete(invite)

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.INVITE_DELETE,
        target_type=AdminAuditTargetType.INVITE,
        target_id=invite_id,
        before=snapshot,
    )
    await db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------


@router.get("/jobs")
async def list_jobs(
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all registered jobs with their status and last run info."""
    from sheaf.models.job_run import JobRun
    from sheaf.services.jobs import get_registry

    registry = get_registry()
    # Ensure jobs are registered even if runner loop hasn't started yet
    if not registry:
        from sheaf.services.jobs import _register_all_jobs
        _register_all_jobs()
        registry = get_registry()

    # Get last run for each job
    jobs = []
    for name, job_def in registry.items():
        result = await db.execute(
            select(JobRun)
            .where(JobRun.job_name == name)
            .order_by(JobRun.started_at.desc())
            .limit(1)
        )
        last_run = result.scalar_one_or_none()

        last_run_info = None
        if last_run is not None:
            duration_ms = None
            if last_run.finished_at and last_run.started_at:
                duration_ms = int(
                    (last_run.finished_at - last_run.started_at).total_seconds() * 1000
                )
            last_run_info = {
                "started_at": last_run.started_at.isoformat(),
                "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
                "status": last_run.status,
                "items_processed": last_run.items_processed,
                "duration_ms": duration_ms,
                "error_message": last_run.error_message,
                "details": last_run.details,
            }

        jobs.append({
            "name": name,
            "description": job_def.description,
            "enabled": job_def.enabled(),
            "interval_seconds": job_def.interval_seconds(),
            "last_run": last_run_info,
        })

    return jobs


@router.post("/jobs/{job_name}/run")
async def trigger_job(
    job_name: str,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a scheduled job. Requires admin:write."""
    from sheaf.services.jobs import get_registry, run_job

    registry = get_registry()
    if job_name not in registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown job: {job_name}",
        )

    run = await run_job(job_name, db)
    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.JOB_TRIGGER,
        target_type=AdminAuditTargetType.JOB,
        after={"job": job_name, "status": run.status},
    )
    await db.commit()

    duration_ms = None
    if run.finished_at and run.started_at:
        duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)

    return {
        "job_name": run.job_name,
        "status": run.status,
        "items_processed": run.items_processed,
        "duration_ms": duration_ms,
        "error_message": run.error_message,
        "details": run.details,
    }


@router.get("/jobs/{job_name}/logs")
async def get_job_logs(
    job_name: str,
    limit: int = 20,
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get recent run history for a job. Requires admin:read."""
    from sheaf.models.job_run import JobRun

    result = await db.execute(
        select(JobRun)
        .where(JobRun.job_name == job_name)
        .order_by(JobRun.started_at.desc())
        .limit(min(limit, 100))
    )
    runs = result.scalars().all()

    return [
        {
            "id": str(run.id),
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "status": run.status,
            "items_processed": run.items_processed,
            "duration_ms": (
                int((run.finished_at - run.started_at).total_seconds() * 1000)
                if run.finished_at and run.started_at
                else None
            ),
            "error_message": run.error_message,
            "details": run.details,
        }
        for run in runs
    ]


# ---------------------------------------------------------------------------
# Account recovery tools
# ---------------------------------------------------------------------------


class AdminResetPasswordRequest(AdminReasonBody):
    new_password: str | None = None  # If omitted, generate a random password


class AdminChangeEmailRequest(AdminReasonBody):
    new_email: str


def _refuse_admin_target(target: User) -> None:
    """Refuse account-recovery mutations against admin accounts.

    Chaining change-email + reset-password is full account takeover; an
    admin being able to silently capture another admin's account is the
    exact escalation the audit log can't compensate for. Recovering a
    genuinely locked-out admin is a deliberate out-of-band operation
    (DB access), not an API call.
    """
    if target.is_admin:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Recovery actions cannot target admin accounts",
        )


@router.post("/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: uuid.UUID,
    body: AdminResetPasswordRequest,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Reset a user's password. Returns the new password once. Requires
    admin:write, a reason, and a non-admin target.

    Revokes all the user's sessions to force re-login. Writes an audit
    row; the new password is never logged anywhere.
    """
    import secrets

    from sheaf.auth.passwords import hash_password
    from sheaf.auth.sessions import delete_all_user_sessions

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _refuse_admin_target(target)

    new_password = body.new_password or secrets.token_urlsafe(16)
    target.password_hash = await hash_password(new_password)
    # Clear any pending password reset tokens
    target.password_reset_token = None
    target.password_reset_sent_at = None

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_PASSWORD_RESET,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        after={"password_was_custom": body.new_password is not None},
    )
    await db.commit()

    # Revoke all sessions so the user must log in with the new password
    sessions_revoked = await delete_all_user_sessions(user_id)

    return {
        "password": new_password,
        "sessions_revoked": sessions_revoked,
    }


@router.post("/users/{user_id}/change-email")
async def admin_change_email(
    user_id: uuid.UUID,
    body: AdminChangeEmailRequest,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Change a user's email address. Requires admin:write, a reason,
    and a non-admin target.

    Updates the encrypted email and blind index. Marks email as verified
    (admin-initiated change is trusted). Checks for conflicts. Writes an
    audit row carrying the new address (the user sees this row on their
    own account activity page, which is the point).
    """
    from sheaf.crypto import blind_index, encrypt

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _refuse_admin_target(target)

    # Normalize before indexing — mirrors self-service /change-email so the
    # same address in different casings produces a single blind index.
    normalized_email = body.new_email.strip().lower()
    new_hash = blind_index(normalized_email)

    # Check for conflicts
    existing = await db.execute(select(User).where(User.email_hash == new_hash))
    conflict = existing.scalar_one_or_none()
    if conflict is not None and conflict.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already in use by another account",
        )

    target.email = encrypt(normalized_email)
    target.email_hash = new_hash
    target.email_verified = True
    # Clear any pending verification tokens
    target.email_verification_token = None
    target.email_verification_sent_at = None

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_EMAIL_CHANGE,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        after={"new_email": normalized_email},
    )
    await db.commit()

    return {"email": normalized_email}


@router.post("/users/{user_id}/disable-totp")
async def admin_disable_totp(
    user_id: uuid.UUID,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable TOTP 2FA on a user's account. Requires admin:write, a
    reason, and a non-admin target.

    Clears the TOTP secret and recovery codes. The user can re-enrol later.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _refuse_admin_target(target)

    if not target.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP is not enabled on this account",
        )

    target.totp_enabled = False
    target.totp_secret = None
    target.recovery_codes = None

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_TOTP_DISABLE,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        before={"totp_enabled": True},
        after={"totp_enabled": False},
    )
    await db.commit()

    return {"disabled": True}


@router.post("/users/{user_id}/verify-email")
async def admin_verify_email(
    user_id: uuid.UUID,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Force-verify a user's email address. Requires admin:write and a
    reason. Admin targets are allowed - this grants nothing that could
    capture an account, it only clears a verification gate.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if target.email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already verified",
        )

    target.email_verified = True
    target.email_verification_token = None
    target.email_verification_sent_at = None

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_EMAIL_VERIFY,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        before={"email_verified": False},
        after={"email_verified": True},
    )
    await db.commit()

    return {"verified": True}


# ---------------------------------------------------------------------------
# Admin cancel deletion
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/cancel-deletion")
async def admin_cancel_deletion(
    user_id: uuid.UUID,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a user's pending account deletion. Requires admin:write
    and a reason. Admin targets are allowed - restoring a colleague's
    account state grants no access to it.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.account_status != AccountStatus.PENDING_DELETION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have a pending deletion",
        )

    requested_at = (
        target.deletion_requested_at.isoformat()
        if target.deletion_requested_at
        else None
    )
    target.account_status = AccountStatus.ACTIVE
    target.deletion_requested_at = None
    target.deletion_reminders_sent = None

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.USER_DELETION_CANCEL,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        before={"deletion_requested_at": requested_at},
        after={"account_status": "active"},
    )
    await db.commit()

    return {"cancelled": True}

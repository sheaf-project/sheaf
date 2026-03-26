import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_admin_user, get_admin_write_user, get_current_user
from sheaf.auth.sessions import check_admin_step_up, set_admin_step_up
from sheaf.config import settings
from sheaf.crypto import decrypt
from sheaf.database import get_db
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User, UserTier
from sheaf.services.file_cleanup import cleanup_orphaned_files
from sheaf.services.front_retention import prune_free_tier_fronts

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
        verified = await check_admin_step_up(user.id)

    return {
        "level": level,
        "verified": verified,
        "totp_enabled": user.totp_enabled,
    }


@router.post("/auth")
async def verify_admin_step_up(
    body: AdminStepUpVerify,
    user: User = Depends(get_current_user),
):
    """Complete admin step-up authentication for this user (any auth method)."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    level = settings.admin_auth_level

    if level == "password":
        if not body.password:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Password required"
            )
        from sheaf.auth.passwords import verify_password
        if not verify_password(body.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect password"
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
        from sheaf.auth import totp
        totp_secret = decrypt(user.totp_secret)
        if not totp.verify_code(totp_secret, body.totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid TOTP code"
            )

    await set_admin_step_up(user.id)
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


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

class AdminUserRead(BaseModel):
    id: str
    email: str
    tier: str
    is_admin: bool
    member_limit: int | None
    storage_used_bytes: int
    member_count: int
    created_at: datetime
    last_login_at: datetime | None


class AdminUserUpdate(BaseModel):
    tier: UserTier | None = None
    is_admin: bool | None = None
    member_limit: int | None = None
    clear_member_limit: bool = False  # set True to reset to tier default (null)


@router.get("/users", response_model=list[AdminUserRead])
async def list_users(
    search: str = "",
    page: int = 1,
    limit: int = 50,
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users with member counts. Requires admin:read scope or is_admin."""
    # Email is encrypted — search must happen after decryption, so we fetch
    # all rows (or all for the unfiltered case), decrypt, filter, then paginate.
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

    rows = await db.execute(query)
    all_users = []
    for user, member_count, storage_used_bytes in rows:
        try:
            email = decrypt(user.email)
        except Exception:
            email = "<encrypted>"

        if search and search.lower() not in email.lower():
            continue

        all_users.append(AdminUserRead(
            id=str(user.id),
            email=email,
            tier=user.tier,
            is_admin=user.is_admin,
            member_limit=user.member_limit,
            storage_used_bytes=storage_used_bytes,
            member_count=member_count,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        ))

    offset = (page - 1) * limit
    return all_users[offset : offset + limit]


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

    if body.tier is not None:
        target.tier = body.tier
    if body.is_admin is not None:
        target.is_admin = body.is_admin
    if body.clear_member_limit:
        target.member_limit = None
    elif body.member_limit is not None:
        target.member_limit = body.member_limit

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
        email = decrypt(target.email)
    except Exception:
        email = "<encrypted>"

    return AdminUserRead(
        id=str(target.id),
        email=email,
        tier=target.tier,
        is_admin=target.is_admin,
        member_limit=target.member_limit,
        storage_used_bytes=storage_used,
        member_count=member_count,
        created_at=target.created_at,
        last_login_at=target.last_login_at,
    )


# ---------------------------------------------------------------------------
# Maintenance operations (kept from before, now using get_admin_write_user)
# ---------------------------------------------------------------------------

@router.post("/retention/run")
async def run_retention(
    _: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger front history retention pruning. Admin only."""
    count = await prune_free_tier_fronts(db)
    return {"pruned": count}


@router.post("/cleanup/run")
async def run_cleanup(
    _: User = Depends(get_admin_write_user),
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
    target.member_limit = body.member_limit
    return {"user_id": str(user_id), "member_limit": target.member_limit}

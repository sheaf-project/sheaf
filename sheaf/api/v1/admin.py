import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_admin_user
from sheaf.database import get_db
from sheaf.models.user import User
from sheaf.services.file_cleanup import audit_all_storage, cleanup_orphaned_files
from sheaf.services.front_retention import prune_free_tier_fronts

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/retention/run")
async def run_retention(
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger front history retention pruning. Admin only.

    Only prunes in aaS mode. Returns 0 in self-hosted mode.
    """
    count = await prune_free_tier_fronts(db)
    return {"pruned": count}


@router.post("/cleanup/run")
async def run_cleanup(
    user: User = Depends(get_admin_user),
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


@router.post("/storage/audit")
async def run_storage_audit(
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Audit and correct storage usage counters for all users. Admin only."""
    return await audit_all_storage(db)


class MemberLimitOverride(BaseModel):
    member_limit: int | None = None  # null = reset to tier default


@router.put("/users/{user_id}/member-limit")
async def set_member_limit(
    user_id: uuid.UUID,
    body: MemberLimitOverride,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Set or reset a user's member limit override. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    target.member_limit = body.member_limit
    return {
        "user_id": str(user_id),
        "member_limit": target.member_limit,
        "note": "null means tier default applies",
    }

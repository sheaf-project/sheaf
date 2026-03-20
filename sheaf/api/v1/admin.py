from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_admin_user
from sheaf.database import get_db
from sheaf.models.user import User
from sheaf.services.file_cleanup import cleanup_orphaned_files
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

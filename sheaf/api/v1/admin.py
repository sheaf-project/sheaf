from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_admin_user
from sheaf.database import get_db
from sheaf.models.user import User
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

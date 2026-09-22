"""Request-scoped helpers shared by the v1 routers.

`get_user_system` used to be copy-pasted into twenty router modules, each one
issuing its own `SELECT ... FROM systems WHERE user_id = ?` on every request.
`get_current_user` now loads the system alongside the user in a single query
(`User.system` is a one-to-one relationship), so the common case here is a
free attribute read. The query only runs for a `User` that arrived some other
way - an admin endpoint loading a different account, a background job - where
nothing eager-loaded it. Checked through SQLAlchemy's instance state rather
than by touching the attribute, because a lazy load on an AsyncSession raises
instead of loading.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.models.system import System
from sheaf.models.user import User


def _loaded_system(user: User) -> System | None:
    """The eager-loaded system, or None if it was never loaded.

    None for "not loaded" and None for "loaded, and there is none" look the
    same to a caller, which is fine: both fall through to the query, and the
    query answers the second case with a 404 exactly as it always did.
    """
    if "system" in inspect(user).unloaded:
        return None
    return user.system


async def get_user_system(user: User, db: AsyncSession) -> System:
    """The system owned by `user`, or 404.

    Same contract as the twenty local `_get_user_system` copies this replaced,
    so every call site keeps working unchanged; the only difference is that a
    user who came through `get_current_user` costs no query here.
    """
    system = _loaded_system(user)
    if system is None:
        result = await db.execute(select(System).where(System.user_id == user.id))
        system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )
    return system


async def get_current_system(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> System:
    """Dependency form of `get_user_system` for new endpoints."""
    return await get_user_system(user, db)

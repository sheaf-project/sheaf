"""Watch tokens (owner-side): create, list, label, revoke."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.notification_channel import NotificationChannel
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.models.watch_token import WatchToken
from sheaf.schemas.notifications import (
    WatchTokenCreate,
    WatchTokenRead,
    WatchTokenUpdate,
)

router = APIRouter(prefix="", tags=["notifications"])


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )
    return system


async def _watch_token_to_read(
    db: AsyncSession, token: WatchToken
) -> WatchTokenRead:
    count_result = await db.execute(
        select(func.count())
        .select_from(NotificationChannel)
        .where(NotificationChannel.watch_token_id == token.id)
    )
    return WatchTokenRead(
        id=token.id,
        system_id=token.system_id,
        label=token.label,
        revoked_at=token.revoked_at,
        created_at=token.created_at,
        updated_at=token.updated_at,
        channel_count=count_result.scalar_one(),
    )


@router.post(
    "/systems/{system_id}/watch-tokens",
    response_model=WatchTokenRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def create_watch_token(
    system_id: uuid.UUID,
    body: WatchTokenCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WatchTokenRead:
    system = await _get_user_system(user, db)
    if system.id != system_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    token = WatchToken(
        id=uuid.uuid4(),
        system_id=system.id,
        label=body.label,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return await _watch_token_to_read(db, token)


@router.get(
    "/systems/{system_id}/watch-tokens",
    response_model=list[WatchTokenRead],
)
async def list_watch_tokens(
    system_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WatchTokenRead]:
    system = await _get_user_system(user, db)
    if system.id != system_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    result = await db.execute(
        select(WatchToken)
        .where(WatchToken.system_id == system.id)
        .order_by(WatchToken.created_at.desc())
    )
    tokens = list(result.scalars().all())
    return [await _watch_token_to_read(db, t) for t in tokens]


async def _load_owned_token(
    db: AsyncSession, user: User, token_id: uuid.UUID
) -> WatchToken:
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(WatchToken).where(
            WatchToken.id == token_id, WatchToken.system_id == system.id
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Watch token not found"
        )
    return token


@router.get(
    "/watch-tokens/{token_id}",
    response_model=WatchTokenRead,
)
async def get_watch_token(
    token_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WatchTokenRead:
    token = await _load_owned_token(db, user, token_id)
    return await _watch_token_to_read(db, token)


@router.patch(
    "/watch-tokens/{token_id}",
    response_model=WatchTokenRead,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def update_watch_token(
    token_id: uuid.UUID,
    body: WatchTokenUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WatchTokenRead:
    token = await _load_owned_token(db, user, token_id)
    if body.label is not None:
        token.label = body.label
    await db.commit()
    await db.refresh(token)
    return await _watch_token_to_read(db, token)


@router.delete(
    "/watch-tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def revoke_watch_token(
    token_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Soft-revoke. Channels remain in the DB but the dispatcher will skip
    them. Owner can revoke and re-issue a fresh token if a recipient
    relationship has gone bad."""
    token = await _load_owned_token(db, user, token_id)
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

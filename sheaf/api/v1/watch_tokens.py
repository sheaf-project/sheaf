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
from sheaf.models.pending_action import PendingActionType
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.models.watch_token import WatchToken
from sheaf.schemas.notifications import (
    WatchTokenCreate,
    WatchTokenRead,
    WatchTokenRevokeConfirm,
    WatchTokenUpdate,
)
from sheaf.services.system_safety import (
    is_safeguarded,
    pending_finalize_after_by_target,
    queue_pending_action,
    verify_destructive_auth,
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
    db: AsyncSession,
    token: WatchToken,
    *,
    pending_delete_at: datetime | None = None,
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
        pending_delete_at=pending_delete_at,
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
    pending = await pending_finalize_after_by_target(
        db, system.id, PendingActionType.WATCH_TOKEN_REVOKE
    )
    return [
        await _watch_token_to_read(db, t, pending_delete_at=pending.get(t.id))
        for t in tokens
    ]


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
    pending = await pending_finalize_after_by_target(
        db, token.system_id, PendingActionType.WATCH_TOKEN_REVOKE
    )
    return await _watch_token_to_read(
        db, token, pending_delete_at=pending.get(token.id)
    )


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
    pending = await pending_finalize_after_by_target(
        db, token.system_id, PendingActionType.WATCH_TOKEN_REVOKE
    )
    return await _watch_token_to_read(
        db, token, pending_delete_at=pending.get(token.id)
    )


@router.delete(
    "/watch-tokens/{token_id}",
    dependencies=[Depends(require_scope("notifications:delete"))],
)
async def revoke_watch_token(
    token_id: uuid.UUID,
    body: WatchTokenRevokeConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Soft-revoke. Channels remain in the DB but the dispatcher will skip
    them. Owner can revoke and re-issue a fresh token if a recipient
    relationship has gone bad."""
    token = await _load_owned_token(db, user, token_id)
    system = await _get_user_system(user, db)
    verify_destructive_auth(
        user,
        system,
        body.password if body else None,
        body.totp_code if body else None,
    )

    # Already revoked -> idempotent 204 regardless of safety state. Skipping
    # the safeguard branch here means re-revoking doesn't queue a duplicate
    # pending action and doesn't lock the owner out of the no-op call.
    if token.revoked_at is not None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if is_safeguarded(system, PendingActionType.WATCH_TOKEN_REVOKE):
        from fastapi.responses import JSONResponse

        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.WATCH_TOKEN_REVOKE,
            target_id=token.id,
            target_label=token.label or f"Watcher {token.id}",
        )
        await db.commit()
        await db.refresh(pending)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "pending_action_id": str(pending.id),
                "finalize_after": pending.finalize_after.isoformat(),
            },
        )

    token.revoked_at = datetime.now(UTC)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

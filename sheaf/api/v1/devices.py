"""Mobile push device-token registration endpoints.

The mobile app calls these to register / refresh / drop the FCM or APNs
push token bound to its currently-logged-in account. See
`mobile-push-architecture.md` in the design-docs repo for the rationale.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.config import settings
from sheaf.database import get_db
from sheaf.models.push_device_token import PushDeviceToken, PushPlatform
from sheaf.models.user import User
from sheaf.schemas.push_device import (
    PushDeviceDeleteRequest,
    PushDeviceRead,
    PushDeviceRegisterRequest,
)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post(
    "/push",
    response_model=PushDeviceRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def register_push_device(
    body: PushDeviceRegisterRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PushDeviceRead:
    """Register or refresh a push token for the calling account.

    Three update paths, in priority order:

    1. Exact match on (account, platform, token): bump last_seen_at
       and update app_version / install_id. Idempotent.
    2. install_id match with a stale token: treat as rotation —
       overwrite the existing row's token in place. This avoids
       leaking dead rows when the platform issues a fresh token for
       the same install (FCM `onNewToken`, APNs delegate re-register).
    3. Otherwise: insert a new row, evicting the oldest-`last_seen_at`
       row first if the per-account soft cap is hit.
    """
    # apns_dev is opt-in. Prod deployments leave APNS_DEV_ENABLED off
    # so sandbox tokens can't be registered against the prod backend
    # (where APNs would bounce them at delivery time anyway). Reject
    # at registration so we don't accrue orphaned dev rows on a prod
    # account; symmetric with the channel-creation gate.
    if (
        body.platform == PushPlatform.APNS_DEV
        and not settings.apns_dev_enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="apns_dev is not enabled on this server",
        )

    now = datetime.now(UTC)

    # Path 1: exact match.
    result = await db.execute(
        select(PushDeviceToken).where(
            PushDeviceToken.account_id == user.id,
            PushDeviceToken.platform == body.platform.value,
            PushDeviceToken.token == body.token,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.last_seen_at = now
        if body.install_id is not None:
            existing.install_id = body.install_id
        if body.app_version is not None:
            existing.app_version = body.app_version
        await db.commit()
        await db.refresh(existing)
        return PushDeviceRead.model_validate(existing)

    # Path 2: install_id match (rotation).
    if body.install_id:
        result = await db.execute(
            select(PushDeviceToken).where(
                PushDeviceToken.account_id == user.id,
                PushDeviceToken.platform == body.platform.value,
                PushDeviceToken.install_id == body.install_id,
            )
        )
        rotating = result.scalar_one_or_none()
        if rotating is not None:
            rotating.token = body.token
            rotating.last_seen_at = now
            if body.app_version is not None:
                rotating.app_version = body.app_version
            await db.commit()
            await db.refresh(rotating)
            return PushDeviceRead.model_validate(rotating)

    # Path 3: insert (with optional LRU eviction).
    cap = settings.notifications_mobile_tokens_per_account_max
    if cap > 0:
        count_result = await db.execute(
            select(PushDeviceToken).where(
                PushDeviceToken.account_id == user.id,
            )
        )
        rows = list(count_result.scalars().all())
        # Evict oldest-last_seen_at rows until we're under cap. Strictly
        # speaking we only need to evict (count - cap + 1) rows to fit
        # the new one; this loop handles backfill if the cap was
        # lowered after the fact.
        rows.sort(key=lambda r: r.last_seen_at)
        while len(rows) >= cap:
            await db.delete(rows[0])
            rows = rows[1:]

    new_row = PushDeviceToken(
        id=uuid.uuid4(),
        account_id=user.id,
        platform=body.platform.value,
        token=body.token,
        install_id=body.install_id,
        app_version=body.app_version,
        last_seen_at=now,
    )
    db.add(new_row)
    await db.commit()
    await db.refresh(new_row)
    return PushDeviceRead.model_validate(new_row)


@router.delete(
    "/push",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def delete_push_device(
    body: PushDeviceDeleteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Drop a push token. Called by the client on logout.

    Idempotent: deleting a token that doesn't exist (already evicted by
    the LRU cap, lazily reaped via 410, etc.) returns 204 anyway. We
    delete by (account_id, token) regardless of platform, so a single
    DELETE clears all platforms for the same token (in practice tokens
    are platform-specific so this is just defense-in-depth)."""
    await db.execute(
        delete(PushDeviceToken).where(
            PushDeviceToken.account_id == user.id,
            PushDeviceToken.token == body.token,
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/push", response_model=list[PushDeviceRead])
async def list_push_devices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PushDeviceRead]:
    """List the calling account's registered push devices.

    Tokens are NOT returned — the client gets metadata only (platform,
    install_id, app_version, timestamps), enough to render a "your
    devices" list with a remove button."""
    result = await db.execute(
        select(PushDeviceToken)
        .where(PushDeviceToken.account_id == user.id)
        .order_by(PushDeviceToken.last_seen_at.desc())
    )
    return [PushDeviceRead.model_validate(r) for r in result.scalars().all()]


__all__ = ["router", "PushPlatform"]

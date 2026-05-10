"""Recipient-facing endpoints. No authentication required.

- POST /v1/notifications/redeem            : redeem an activation code,
  attach a push subscription, get a management URL.
- GET  /v1/notifications/manage/{token}    : view the channel.
- POST /v1/notifications/manage/{token}/unsubscribe : disable the channel.

These endpoints accept anonymous traffic. If the redeemer is currently
signed in (session cookie present), `redeemed_by_account_id` is also set
so future cross-system management UIs can list this subscription.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.sessions import get_session_user_id
from sheaf.config import settings
from sheaf.database import get_db
from sheaf.models.notification_channel import (
    DestinationState,
    DestinationType,
    NotificationChannel,
)
from sheaf.models.system import System
from sheaf.models.watch_token import WatchToken
from sheaf.schemas.notifications import (
    ManageChannelView,
    RedeemRequest,
    RedeemResponse,
)
from sheaf.services.notifications.activation import (
    activation_code_matches,
    hash_management_token,
    issue_management_token,
)
from sheaf.services.notifications.activation import (
    management_url as build_management_url,
)

router = APIRouter(prefix="/notifications", tags=["notifications-public"])


@router.post("/redeem", response_model=RedeemResponse)
async def redeem_activation(
    body: RedeemRequest,
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
    db: AsyncSession = Depends(get_db),
) -> RedeemResponse:
    # Find a candidate channel by hashing the code and looking it up. The
    # hash is keyed (jwt_secret_key) so an attacker without the server key
    # can't precompute a rainbow table.
    from sheaf.services.notifications.activation import hash_activation_code

    code_hash = hash_activation_code(body.activation_code)
    result = await db.execute(
        select(NotificationChannel).where(
            NotificationChannel.activation_code_hash == code_hash
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None or not activation_code_matches(
        body.activation_code, channel.activation_code_hash or ""
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invalid activation code"
        )

    if channel.destination_state != DestinationState.PENDING_REGISTRATION.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Channel is no longer pending registration",
        )
    if (
        channel.activation_code_expires_at is not None
        and channel.activation_code_expires_at < datetime.now(UTC)
    ):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Activation code has expired",
        )

    mobile_push_types = {
        DestinationType.FCM.value,
        DestinationType.APNS_DEV.value,
        DestinationType.APNS_PROD.value,
    }
    is_mobile = channel.destination_type in mobile_push_types

    if channel.destination_type == DestinationType.WEB_PUSH.value:
        if body.push_subscription is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="push_subscription required for web_push channels",
            )
        channel.destination_config = body.push_subscription.model_dump()  # noqa: SIM102

    # Resolve the redeemer's account from the session cookie.
    redeemer_account_id = None
    if session_id is not None:
        redeemer_account_id = await get_session_user_id(session_id)

    if is_mobile:
        # Mobile push is account-anchored: a session is required, and the
        # transport (push token) lives on push_device_tokens, not the
        # channel. Refuse any push_subscription supplied by the client.
        if redeemer_account_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="login required to redeem a mobile push channel",
            )
        if body.push_subscription is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="push_subscription is not used for mobile push channels",
            )
        channel.destination_config = {}
        channel.redeemed_by_account_id = redeemer_account_id
    elif redeemer_account_id is not None:
        # Web push: optional account-link if the redeemer is signed in.
        channel.redeemed_by_account_id = redeemer_account_id

    if not is_mobile:
        issued = issue_management_token()
        channel.recipient_management_token_hash = issued.token_hash
        management_url = build_management_url(
            settings.sheaf_base_url or "", issued.token
        )
    else:
        # No anonymous /manage URL for mobile push — recipients manage
        # via the in-app Receiving screen using the existing
        # /notifications/receiving/{channel_id}/unsubscribe endpoint
        # under their logged-in session.
        management_url = ""

    channel.activation_code_hash = None
    channel.activation_code_expires_at = None
    channel.redeemed_at = datetime.now(UTC)
    channel.destination_state = DestinationState.ACTIVE.value
    await db.commit()

    # Look up watch token + system for the response label.
    token_result = await db.execute(
        select(WatchToken).where(WatchToken.id == channel.watch_token_id)
    )
    token = token_result.scalar_one()
    system_result = await db.execute(select(System).where(System.id == token.system_id))
    system = system_result.scalar_one_or_none()
    system_label = (
        getattr(system, "display_name", None) if system is not None else None
    )

    return RedeemResponse(
        management_url=management_url,
        channel_name=channel.name,
        system_label=system_label,
    )


async def _channel_by_management_token(
    db: AsyncSession,
    token: str,
    *,
    session_id: str | None,
) -> NotificationChannel:
    """Resolve a channel by its management URL token.

    For account-bound channels (`redeemed_by_account_id` is set), additionally
    require the request to carry a session for that user. The capability URL
    alone is no longer sufficient; a leaked URL won't unsubscribe an
    account-linked recipient unless the attacker also has the account's
    session. Anonymous channels keep the pure capability-URL semantics so
    recipients without Sheaf accounts can still manage their subscription.
    """
    token_hash = hash_management_token(token)
    result = await db.execute(
        select(NotificationChannel).where(
            NotificationChannel.recipient_management_token_hash == token_hash
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invalid management token"
        )
    if channel.redeemed_by_account_id is not None:
        session_user_id = (
            await get_session_user_id(session_id) if session_id else None
        )
        if session_user_id != channel.redeemed_by_account_id:
            # Don't disclose whether the channel exists at all when the
            # caller isn't the bound recipient.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invalid management token",
            )
    return channel


@router.get("/manage/{mgmt_token}", response_model=ManageChannelView)
async def view_managed(
    mgmt_token: str,
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
    db: AsyncSession = Depends(get_db),
) -> ManageChannelView:
    channel = await _channel_by_management_token(
        db, mgmt_token, session_id=session_id
    )
    token_result = await db.execute(
        select(WatchToken).where(WatchToken.id == channel.watch_token_id)
    )
    token = token_result.scalar_one_or_none()
    system_label = None
    if token is not None:
        system_result = await db.execute(
            select(System).where(System.id == token.system_id)
        )
        system = system_result.scalar_one_or_none()
        if system is not None:
            system_label = getattr(system, "display_name", None)
    return ManageChannelView(
        channel_id=channel.id,
        channel_name=channel.name,
        system_label=system_label,
        destination_type=channel.destination_type,
        destination_state=channel.destination_state,
    )


@router.post(
    "/manage/{mgmt_token}/unsubscribe",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unsubscribe(
    mgmt_token: str,
    session_id: str | None = Cookie(default=None, alias="sheaf_session"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    channel = await _channel_by_management_token(
        db, mgmt_token, session_id=session_id
    )
    channel.destination_state = DestinationState.DISABLED.value
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

"""Shield-mode endpoints.

Two public-facing routes plus one internal webhook:

  - `GET /v1/shield-mode/status`: unauthenticated, lightweight. Returns
    the current `active` flag and (if known) the timestamp of the last
    transition, plus a `feature_enabled` flag that the frontend uses to
    decide whether to render the Privacy/Security toggle at all. Always
    safe to call; cheap to poll from mobile clients that want to honor
    their user's opt-out preference voluntarily.

  - `POST /v1/internal/shield-mode/state`: HMAC-authenticated webhook
    from the operator's cf-shield script. Body `{"active": bool}`. Only
    accepted when `settings.shield_mode_enabled=true`; otherwise 404 so
    the URL doesn't even appear to exist.

The webhook is intentionally not behind `require_scope` - it isn't a
user-facing action. Authentication is the HMAC signature alone, which
the script computes from the raw body using a shared secret kept in
SSM.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.database import get_db
from sheaf.observability.metrics import webhook_signature_failures_total
from sheaf.services.shield_mode import (
    SIGNATURE_HEADER,
    apply_transition,
    get_state,
    verify_signature,
)

logger = logging.getLogger("sheaf.shield_mode.api")

router = APIRouter(tags=["shield mode"])


class ShieldModeStatus(BaseModel):
    """Public status payload.

    `feature_enabled` mirrors the operator-side config and lets the
    frontend hide the user-facing toggle on instances that aren't wired
    to a cf-shield setup. `active` is always truthful regardless: it
    reflects whatever Redis says, defaulting to false.
    """

    feature_enabled: bool
    active: bool
    since: str | None = None


class ShieldModeTransitionRequest(BaseModel):
    active: bool


@router.get("/shield-mode/status", response_model=ShieldModeStatus)
async def shield_mode_status() -> ShieldModeStatus:
    if not settings.shield_mode_enabled:
        # Truthful: feature is dormant, so by definition no shield is up.
        return ShieldModeStatus(feature_enabled=False, active=False, since=None)
    state = await get_state()
    return ShieldModeStatus(
        feature_enabled=True,
        active=state.active,
        since=state.since.isoformat() if state.since else None,
    )


@router.post(
    "/internal/shield-mode/state",
    response_model=ShieldModeStatus,
)
async def shield_mode_transition(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ShieldModeStatus:
    if not settings.shield_mode_enabled:
        # Don't leak that the route exists at all when the feature is
        # off. The script knows what configuration it expects.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    body = await request.body()
    sig = request.headers.get(SIGNATURE_HEADER)
    if not verify_signature(body, sig):
        # Generic 401 - don't distinguish "missing header" from
        # "wrong digest" so a probe can't fingerprint the secret.
        webhook_signature_failures_total.labels(endpoint="cf_shield").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    try:
        parsed = ShieldModeTransitionRequest.model_validate_json(body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed body: {exc}",
        ) from exc

    new_state = await apply_transition(active=parsed.active, db=db)
    return ShieldModeStatus(
        feature_enabled=True,
        active=new_state.active,
        since=new_state.since.isoformat() if new_state.since else None,
    )

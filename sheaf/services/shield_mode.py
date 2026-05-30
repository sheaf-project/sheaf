"""Shield-mode state machine + opt-out enforcement.

When the operator runs `cf-shield up`, the script POSTs to
`POST /v1/internal/shield-mode/state` with an HMAC-signed body. The
endpoint forwards the parsed `active` flag here, which:

  - Persists `{active, since}` in Redis so other backend code (e.g.
    the status endpoint, the login flow, future banners) can read it
    without round-tripping Cloudflare.
  - On the up edge specifically, runs the mass-invalidate pass: every
    user with `User.disable_cdn_during_ddos=true` has all of their
    sessions deleted via the existing `delete_all_user_sessions`
    primitive. They cannot reach the API afterwards without re-auth,
    and the re-auth itself is gated by shield mode (CDN + UAM
    challenge), so the opt-out is effectively enforced for the
    duration of the incident.

Redis is acceptable transient storage here. If the key is lost
mid-shield, the worst case is opted-out users could log back in
through the CDN challenge until either the backend restarts (no
shield-state knowledge) or the script re-sends `up`. The CF side is
the source of truth; the script can be re-run safely.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.sessions import delete_all_user_sessions, get_redis
from sheaf.config import settings
from sheaf.models.user import User

logger = logging.getLogger("sheaf.shield_mode")

_STATE_KEY = "sheaf:shield_mode:state"

# Signature header convention mirrors webhook auth elsewhere in the
# codebase (X-Sheaf-Signature on the notification-channel webhook). The
# script computes `hex(hmac_sha256(secret, body))` and the receiver
# rejects on mismatch with constant-time compare.
SIGNATURE_HEADER = "X-Sheaf-Signature"


@dataclass(frozen=True)
class ShieldState:
    active: bool
    # ISO-8601 timestamp of the most recent transition. None if we have
    # no record (Redis was flushed, or shield has never been engaged).
    since: datetime | None


async def get_state() -> ShieldState:
    """Read the current shield state from Redis. Default off if unset."""
    r = await get_redis()
    raw = await r.get(_STATE_KEY)
    if not raw:
        return ShieldState(active=False, since=None)
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Corrupted state - treat as off rather than crashing the
        # status endpoint. The next `up` from the script will overwrite.
        logger.warning("shield_mode: failed to parse state %r, treating as off", raw)
        return ShieldState(active=False, since=None)
    since_raw = parsed.get("since")
    since = datetime.fromisoformat(since_raw) if since_raw else None
    return ShieldState(active=bool(parsed.get("active", False)), since=since)


async def _write_state(state: ShieldState) -> None:
    r = await get_redis()
    payload = json.dumps(
        {
            "active": state.active,
            "since": state.since.isoformat() if state.since else None,
        }
    )
    # No TTL - we want this to persist across container restarts. The
    # cf-shield script owns the lifecycle; if Redis loses the key, the
    # next operator action re-syncs.
    await r.set(_STATE_KEY, payload)


async def apply_transition(*, active: bool, db: AsyncSession) -> ShieldState:
    """Set shield to `active` and run the side-effects of any transition.

    Idempotent: re-posting `up` while already up is a no-op (no second
    invalidation pass, no `since` rewrite). Returns the resulting state.
    """
    current = await get_state()
    now = datetime.now(UTC)

    if active == current.active:
        # No transition - leave `since` alone so the timestamp reflects
        # the original engagement, not the most recent ping.
        return current

    new_state = ShieldState(active=active, since=now)
    await _write_state(new_state)

    if active:
        # Up edge: run the mass-invalidate pass. Anything that fails
        # here is logged but does not roll back the state flip - the
        # operator-side reality is that CF is already in under-attack
        # mode, so the state is correct even if we couldn't kick a
        # particular user.
        revoked = await _invalidate_opted_out_users(db)
        logger.warning(
            "shield_mode: engaged at %s, invalidated %d opted-out user(s)",
            now.isoformat(),
            revoked,
        )
    else:
        logger.warning("shield_mode: disengaged at %s", now.isoformat())

    return new_state


async def _invalidate_opted_out_users(db: AsyncSession) -> int:
    """Delete sessions for every user with disable_cdn_during_ddos=true.

    Returns the count of users whose sessions were touched (not the
    count of sessions; one user may have multiple). The existing
    `delete_all_user_sessions` returns the per-user session count which
    we sum and log for visibility.
    """
    result = await db.execute(
        select(User.id).where(User.disable_cdn_during_ddos.is_(True))
    )
    user_ids: list[uuid.UUID] = [row[0] for row in result]

    if not user_ids:
        return 0

    total_sessions = 0
    for user_id in user_ids:
        try:
            total_sessions += await delete_all_user_sessions(user_id)
        except Exception:
            # Don't let one user's stuck Redis pipeline starve the rest.
            logger.exception(
                "shield_mode: failed to invalidate sessions for user %s", user_id
            )

    logger.info(
        "shield_mode: revoked %d session(s) across %d opted-out user(s)",
        total_sessions,
        len(user_ids),
    )
    return len(user_ids)


def verify_signature(body: bytes, header_value: str | None) -> bool:
    """Constant-time HMAC verification of the webhook body.

    `body` is the raw request body bytes (FastAPI exposes this via
    `await request.body()`). `header_value` is the X-Sheaf-Signature
    header (hex digest). Returns False on any structural problem -
    missing secret, missing header, wrong length - so callers can 401
    uniformly.
    """
    secret = settings.shield_mode_webhook_secret
    if not secret:
        return False
    if not header_value:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header_value.strip())

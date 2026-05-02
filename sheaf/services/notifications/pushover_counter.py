"""Per-month counters for shared-app Pushover deliveries.

Two tiers of counter:

- **Deployment-wide** (`pushover:usage:YYYY-MM`) — protects the operator's
  Pushover account quota from total runaway. Set by `PUSHOVER_MAX_PER_MONTH`.
- **Per-Sheaf-user** (`pushover:usage:user:{user_id}:YYYY-MM`) — stops one
  user from monopolising the deployment quota; capped per their Sheaf tier
  via `PUSHOVER_USER_MAX_PER_MONTH_{FREE,PLUS,SELF_HOSTED}`.

Both counters increment on the same shared-app delivery. BYO-app channels
(recipient supplies their own app_token) bypass both — they're on the
recipient's own Pushover quota, not ours. Keys auto-expire 45 days after
first write so old counters self-clean.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sheaf.auth.sessions import get_redis
from sheaf.config import settings
from sheaf.models.user import UserTier


def _month_suffix(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m")


def _deployment_key(now: datetime | None = None) -> str:
    return f"pushover:usage:{_month_suffix(now)}"


def _user_key(user_id: uuid.UUID, now: datetime | None = None) -> str:
    return f"pushover:usage:user:{user_id}:{_month_suffix(now)}"


def _ttl_seconds() -> int:
    """Long enough that the counter survives the entire current month plus
    a safety margin into the next, so we never roll over to a 0 count from
    an early eviction."""
    return 45 * 24 * 3600  # 45 days


def cap_for_tier(tier: str | None) -> int:
    """Resolve the per-user monthly cap for a Sheaf tier. Returns 0 for
    unknown tiers and for explicitly-unlimited tiers — the dispatcher
    treats 0 as "skip the check, only the deployment cap applies"."""
    if tier == UserTier.FREE.value:
        return settings.pushover_user_max_per_month_free
    if tier == UserTier.PLUS.value:
        return settings.pushover_user_max_per_month_plus
    if tier == UserTier.SELF_HOSTED.value:
        return settings.pushover_user_max_per_month_self_hosted
    return 0


# --- Deployment-wide counter -------------------------------------------------


async def get_monthly_count(now: datetime | None = None) -> int:
    r = await get_redis()
    val = await r.get(_deployment_key(now))
    return int(val) if val is not None else 0


async def increment_monthly_count(now: datetime | None = None) -> int:
    r = await get_redis()
    key = _deployment_key(now)
    new = await r.incr(key)
    if new == 1:
        await r.expire(key, _ttl_seconds())
    return int(new)


async def is_over_cap(now: datetime | None = None) -> bool:
    """True if shared-app Pushover deliveries should be paused for the
    current month deployment-wide. cap=0 disables the check."""
    cap = settings.pushover_max_per_month
    if cap <= 0:
        return False
    return await get_monthly_count(now) >= cap


# --- Per-user counter --------------------------------------------------------


async def get_user_monthly_count(
    user_id: uuid.UUID, now: datetime | None = None
) -> int:
    r = await get_redis()
    val = await r.get(_user_key(user_id, now))
    return int(val) if val is not None else 0


async def increment_user_monthly_count(
    user_id: uuid.UUID, now: datetime | None = None
) -> int:
    r = await get_redis()
    key = _user_key(user_id, now)
    new = await r.incr(key)
    if new == 1:
        await r.expire(key, _ttl_seconds())
    return int(new)


async def is_user_over_cap(
    user_id: uuid.UUID, tier: str | None, now: datetime | None = None
) -> bool:
    """True if this user has exhausted their per-tier monthly Pushover
    allowance on the shared app. cap=0 disables the per-user check for
    that tier (e.g. self_hosted has no per-user limit by default)."""
    cap = cap_for_tier(tier)
    if cap <= 0:
        return False
    return await get_user_monthly_count(user_id, now) >= cap

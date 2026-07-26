"""Realtime front-change stream: publish side, framing, and shared helpers.

The account itself streams its OWN front changes in near-realtime over SSE
(GET /v1/fronts/stream, in sheaf/api/v1/front_stream.py). This is a first-
party fast path off the same emit point as the third-party watch-token
notifications - it does not touch the outbox or watch-token delivery.

Fanout is Redis pub/sub on a per-system channel, because the app runs as
several single-process uvicorn replicas: a change committed on replica A
must reach a connection held on replica B. See
../sheaf-design-docs/realtime-front-stream.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.sessions import get_redis
from sheaf.config import settings
from sheaf.models.user import User, UserTier
from sheaf.observability.metrics import (
    realtime_events_published_total,
    realtime_publish_failures_total,
)
from sheaf.services.notifications.events import FrontState

logger = logging.getLogger("sheaf")


# ---------------------------------------------------------------------------
# Shutdown signal. Set by the app lifespan on shutdown so live generators can
# close with reason="server_shutdown" instead of looking like a client drop.
# ---------------------------------------------------------------------------

_shutdown_event = asyncio.Event()


def signal_shutdown() -> None:
    """Ask all live front-stream generators to close (server shutting down)."""
    _shutdown_event.set()


def shutdown_requested() -> bool:
    return _shutdown_event.is_set()


# ---------------------------------------------------------------------------
# Channel + key naming
# ---------------------------------------------------------------------------

def front_channel(system_id: uuid.UUID) -> str:
    """Per-system Redis pub/sub channel a front change is published to. One
    channel per system so a multi-system account subscribes to its authorized
    set with no re-filtering."""
    return f"sheaf:fronts:{system_id}"


def connection_slots_key(account_key: str) -> str:
    """Redis key holding an account's live front-stream connections as a sorted
    set: member = a per-connection token, score = that connection's expiry
    deadline. Keyed on the account, never the system, so the cap stays correct
    once one account maps to several systems.

    Note the key name differs from the old integer-counter key so a deploy from
    the counter version does not hit WRONGTYPE against a leftover INCR value -
    the old key simply ages out on its own TTL."""
    return f"sheaf:stream:slots:{account_key}"


# Acquire a connection slot atomically. The cap is enforced over a sorted set
# scored by each connection's expiry deadline: we prune expired members
# (ZREMRANGEBYSCORE) BEFORE counting, so a slot whose owner died without
# releasing it (hard crash, worker SIGKILL, a disconnect whose teardown never
# ran) is reclaimed independently of any other live connection on the same
# account. The old single-counter design could not do that - one leaked slot
# could wedge the cap indefinitely for a client that stays connected 24/7,
# which is the intended use. Done in one EVAL so concurrent acquires cannot
# both pass the cap check (the count and the add are atomic).
_ACQUIRE_SLOT_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local member = ARGV[2]
local score = tonumber(ARGV[3])
local cap = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local count = redis.call('ZCARD', key)
if cap > 0 and count >= cap then
    return {0, count}
end
redis.call('ZADD', key, score, member)
redis.call('EXPIRE', key, ttl)
return {1, count + 1}
"""


async def acquire_stream_slot(
    r, account_key: str, cap: int, now: float, ttl: int
) -> tuple[bool, int, str]:
    """Try to take a connection slot for `account_key`.

    Returns (accepted, live_count, member). `cap <= 0` means unlimited. On
    rejection nothing is added. `member` identifies this connection so it can
    refresh and release its own slot later. `now` is wall-clock seconds
    (time.time()), matching the score scale the prune compares against."""
    member = uuid.uuid4().hex
    accepted, count = await r.eval(
        _ACQUIRE_SLOT_LUA,
        1,
        connection_slots_key(account_key),
        now,
        member,
        now + ttl,
        cap,
        ttl,
    )
    return bool(accepted), int(count), member


async def refresh_stream_slot(
    r, account_key: str, member: str, now: float, ttl: int
) -> None:
    """Extend this connection's slot (called on each heartbeat) and prune any
    slots whose owners have died, so the live count stays honest for everyone
    else on the account. `now` is wall-clock seconds (time.time())."""
    key = connection_slots_key(account_key)
    await r.zadd(key, {member: now + ttl})
    await r.zremrangebyscore(key, "-inf", now)
    await r.expire(key, ttl)


async def release_stream_slot(r, account_key: str, member: str) -> None:
    """Drop this connection's slot on disconnect. Best-effort: if it does not
    run (hard kill), the member's score lapses and the next acquire/heartbeat
    on the account prunes it."""
    await r.zrem(connection_slots_key(account_key), member)


def max_front_stream_connections_for_tier(tier: UserTier | str) -> int:
    """Per-account concurrent front-stream connection cap for the tier.

    0 = unlimited. Self-hosted is unlimited by default (the default tier is
    SELF_HOSTED); the hosted service sets FREE / PLUS. Mirrors the other
    per-tier caps (member_limit_*, storage_quota_*, poll caps)."""
    if tier == UserTier.PLUS:
        return settings.front_stream_max_connections_plus
    if tier == UserTier.SELF_HOSTED:
        return settings.front_stream_max_connections_selfhosted
    return settings.front_stream_max_connections_free


# ---------------------------------------------------------------------------
# Serialization: match the GET /v1/fronts member shape
# ---------------------------------------------------------------------------

def serialize_front_state(state: FrontState) -> list[str]:
    """Render a FrontState to the wire shape the stream uses for a fronting
    set: a sorted list of member-id strings. This mirrors how
    `FrontRead.member_ids` serialises a UUID (str) and how
    `emit_front_change` already renders `fronting_before/after`, so the
    stream payload matches the REST projection field-for-field."""
    return sorted(str(m) for m in state.fronting_member_ids)


def build_change_payload(
    system_id: uuid.UUID,
    before: FrontState,
    after: FrontState,
    *,
    changed_at: datetime,
    event_id: uuid.UUID,
    emit_ts: float,
) -> dict:
    """The `front_change` event body. `emit_ts` is a wall-clock stamp used
    only to measure delivery lag at the client write; `changed_at` is the
    human-facing transition time and doubles as the SSE `id:`."""
    return {
        "system_id": str(system_id),
        "before": serialize_front_state(before),
        "after": serialize_front_state(after),
        "changed_at": changed_at.isoformat(),
        "event_id": str(event_id),
        "emit_ts": emit_ts,
    }


def build_snapshot_payload(system_id: uuid.UUID, state: FrontState) -> dict:
    """The `snapshot` event body sent first on connect so the client is
    correct with no race."""
    return {
        "system_id": str(system_id),
        "fronting": serialize_front_state(state),
        "event_id": str(uuid.uuid4()),
    }


# ---------------------------------------------------------------------------
# SSE framing (pure, unit-testable)
# ---------------------------------------------------------------------------

def format_sse(data: str, *, event: str | None = None, id: str | None = None) -> str:
    """Frame one SSE message. `data` is emitted as one or more `data:` lines
    (splitting on newlines per the SSE spec) and the block is terminated by a
    blank line."""
    lines: list[str] = []
    if id is not None:
        lines.append(f"id: {id}")
    if event is not None:
        lines.append(f"event: {event}")
    for chunk in (data.split("\n") or [""]):
        lines.append(f"data: {chunk}")
    return "\n".join(lines) + "\n\n"


def format_comment(text: str = "ping") -> str:
    """An SSE comment line - a heartbeat that keeps proxies open and surfaces
    a dead peer without being delivered to the EventSource `onmessage`."""
    return f": {text}\n\n"


# ---------------------------------------------------------------------------
# Authorized-system-set resolver (subsystem-safe)
# ---------------------------------------------------------------------------

async def authorized_front_system_ids(
    user: User, db: AsyncSession
) -> list[uuid.UUID]:
    """Systems this principal may read fronts for.

    Today `System.user_id` is unique so this is the account's single system,
    but it is deliberately a set resolver: when one account maps to many
    systems, this returns the collection and nothing on the stream path has
    to change. Never inline `System.user_id == user.id` in the stream itself.
    """
    from sheaf.models.system import System

    result = await db.execute(select(System.id).where(System.user_id == user.id))
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Publish point (called after commit, never inside the transaction)
# ---------------------------------------------------------------------------

async def publish_front_change(
    system_id: uuid.UUID, before: FrontState, after: FrontState
) -> None:
    """Publish a front-change event to the per-system Redis channel.

    Best-effort: a Redis failure is logged and counted but never raised, so a
    down Redis degrades the stream to nothing without failing the front
    switch. Call this AFTER `db.commit()` - a rolled-back switch must not
    emit a phantom event.
    """
    payload = build_change_payload(
        system_id,
        before,
        after,
        changed_at=datetime.now(UTC),
        event_id=uuid.uuid4(),
        emit_ts=time.time(),
    )
    try:
        r = await get_redis()
        await r.publish(front_channel(system_id), json.dumps(payload))
        realtime_events_published_total.inc()
    except Exception:
        realtime_publish_failures_total.inc()
        logger.warning(
            "front-stream publish failed for system %s (event dropped)",
            system_id,
            exc_info=True,
        )

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
from sheaf.models.user import User
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


def connection_count_key(account_key: str) -> str:
    """Redis key holding an account's live-stream connection count. Keyed on
    the account, never the system, so the cap stays correct once one account
    maps to several systems."""
    return f"sheaf:stream:conns:{account_key}"


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

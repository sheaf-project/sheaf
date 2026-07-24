"""Realtime front-change stream endpoint: GET /v1/fronts/stream (SSE).

A first-party, near-realtime feed of an account's OWN front changes for
home-automation integrations (Home Assistant / Node-RED) and future web-UI
live updates. Authenticated by an API key with `fronts:read` or a browser
session; the same data as `GET /v1/fronts`, pushed instead of polled.

The stream helpers, publish side, and framing live in
sheaf/services/front_stream.py. See ../sheaf-design-docs/realtime-front-stream.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.auth.sessions import get_redis, get_session_user_id
from sheaf.config import settings
from sheaf.database import async_session_factory, get_db
from sheaf.models.user import User
from sheaf.observability.metrics import (
    realtime_connection_duration_seconds,
    realtime_connections_active,
    realtime_connections_closed_total,
    realtime_connections_opened_total,
    realtime_delivery_lag_seconds,
    realtime_events_delivered_total,
    realtime_events_dropped_total,
    realtime_handshake_failures_total,
)
from sheaf.services.front_stream import (
    authorized_front_system_ids,
    build_snapshot_payload,
    connection_count_key,
    format_comment,
    format_sse,
    front_channel,
    shutdown_requested,
)
from sheaf.services.notifications.events import snapshot_front_state

logger = logging.getLogger("sheaf")

router = APIRouter(prefix="/fronts", tags=["fronts"])

# Bound on the per-connection relay queue. Front changes are small and
# low-frequency, so a client this far behind is not keeping up: we close it
# (backpressure) and let it reconnect and re-snapshot rather than buffer
# unboundedly. Snapshots are idempotent, so no meaningful event is lost.
_QUEUE_MAXSIZE = 100


def _ensure_fronts_read_scope(request: Request) -> None:
    """Enforce `fronts:read` for API-key auth, counting a rejection on the
    handshake-failure metric.

    Mirrors `require_scope("fronts:read")` (write/delete imply read;
    session/JWT auth is unrestricted). It is inlined here rather than used as
    a `Depends` so the missing-scope rejection can be attributed to the
    stream's handshake metric - a router-level dependency raises before the
    handler runs, where the counter is out of reach.
    """
    scopes = request.state.api_key_scopes
    if scopes is None:
        return  # session / JWT: unrestricted
    if (
        "fronts:read" in scopes
        or "fronts:write" in scopes
        or "fronts:delete" in scopes
    ):
        return
    realtime_handshake_failures_total.labels(reason="missing_scope").inc()
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Missing scope: fronts:read",
    )


def _auth_context(request: Request) -> dict:
    """Snapshot the caller's credential identity for mid-stream re-checks.

    Captured while the request is live because the generator runs after the
    request's DB session has closed and `request.state` is no longer being
    populated.
    """
    return {
        "method": getattr(request.state, "auth_method", None),
        "api_key_id": getattr(request.state, "api_key_id", None),
        "session_id": getattr(request.state, "session_id", None),
    }


async def _recheck_auth(ctx: dict) -> tuple[bool, str | None]:
    """Re-validate the connection's credential. Returns (ok, close_reason).

    API keys have no liveness cache (hard-DELETE on revoke, `expires_at`
    checked only at auth time), so we re-SELECT the row: gone = revoked, past
    expiry = expired. Session-authed streams lean on the existing Redis
    session liveness. A bare (non-session) JWT has neither, so it rides its
    own short access-token lifetime - nothing to re-check here.
    """
    method = ctx.get("method")
    if method == "api_key":
        from datetime import UTC, datetime

        from sheaf.models.api_key import ApiKey

        async with async_session_factory() as db:
            row = await db.get(ApiKey, ctx["api_key_id"])
        if row is None:
            return False, "auth_revoked"
        if row.expires_at is not None and datetime.now(UTC) > row.expires_at:
            return False, "auth_expired"
        return True, None

    session_id = ctx.get("session_id")
    if session_id is not None:
        if await get_session_user_id(session_id) is None:
            return False, "auth_revoked"
        return True, None

    return True, None


async def _pubsub_reader(pubsub, queue: asyncio.Queue, state: dict) -> None:
    """Drain the Redis subscription into the bounded relay queue.

    Runs as its own task so the SSE write path (which blocks on a slow
    client) can't stall message intake. On queue overflow we flag backpressure
    and stop; on any Redis error we flag it - the main loop turns either into
    a clean close.
    """
    try:
        while True:
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if msg is None:
                continue
            data = msg.get("data")
            try:
                payload = json.loads(data)
            except (TypeError, ValueError):
                continue
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                state["overflow"] = True
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("front-stream reader error", exc_info=True)
        state["error"] = True


async def _stream(
    account_key: str,
    system_ids: list[uuid.UUID],
    auth_ctx: dict,
):
    """The SSE body. Guarantees, on every exit path (client disconnect,
    error, revocation, backpressure, shutdown), that the pub/sub connection
    is closed, the connection-cap counter is decremented, the active-
    connections gauge is decremented, and the duration + close reason are
    recorded - all in the `finally`.
    """
    heartbeat = settings.front_stream_heartbeat_seconds
    authcheck = settings.front_stream_auth_recheck_seconds
    # Refreshed each heartbeat so a live connection keeps its cap slot alive;
    # if every connection for an account dies without running `finally` (hard
    # crash), the counter self-heals when the key expires. Comfortably longer
    # than the heartbeat that refreshes it.
    cap_ttl = max(heartbeat * 5, 60)

    r = await get_redis()
    pubsub = r.pubsub()
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    reader: asyncio.Task | None = None
    state: dict = {}
    reason = "client_closed"
    started = time.monotonic()

    realtime_connections_active.inc()
    realtime_connections_opened_total.inc()
    try:
        if system_ids:
            await pubsub.subscribe(*(front_channel(sid) for sid in system_ids))

        # Start the reader BEFORE snapshotting so a change that lands during
        # the snapshot is buffered, not lost: subscribe-before-snapshot closes
        # the connect-time lost-update race. A delta the snapshot already
        # reflects is at worst delivered redundantly (its `after` is the same
        # state), never missed.
        reader = asyncio.create_task(_pubsub_reader(pubsub, queue, state))

        # Snapshot in the generator's own DB session (the request session has
        # closed by now) so the client is correct with no race, then deltas.
        async with async_session_factory() as db:
            for sid in system_ids:
                snap = build_snapshot_payload(
                    sid, await snapshot_front_state(db, sid)
                )
                yield format_sse(
                    json.dumps(snap), event="snapshot", id=snap["event_id"]
                )

        now = time.monotonic()
        next_heartbeat = now + heartbeat
        next_authcheck = now + authcheck

        while True:
            if shutdown_requested():
                reason = "server_shutdown"
                break
            if state.get("overflow"):
                realtime_events_dropped_total.labels(reason="backpressure").inc()
                reason = "backpressure"
                break
            if state.get("error"):
                reason = "error"
                break

            now = time.monotonic()
            timeout = max(0.0, min(next_heartbeat, next_authcheck) - now)
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=timeout)
            except TimeoutError:
                payload = None

            if payload is not None:
                yield format_sse(
                    json.dumps(payload),
                    event="front_change",
                    id=str(payload.get("changed_at", "")),
                )
                realtime_events_delivered_total.inc()
                emit_ts = payload.get("emit_ts")
                if isinstance(emit_ts, (int, float)):
                    realtime_delivery_lag_seconds.observe(
                        max(0.0, time.time() - emit_ts)
                    )

            now = time.monotonic()
            if now >= next_heartbeat:
                yield format_comment()
                # Keep the account's cap slot from expiring under us.
                await r.expire(connection_count_key(account_key), cap_ttl)
                next_heartbeat = now + heartbeat
            if now >= next_authcheck:
                ok, close_reason = await _recheck_auth(auth_ctx)
                if not ok:
                    reason = close_reason or "auth_revoked"
                    break
                next_authcheck = now + authcheck
    except asyncio.CancelledError:
        # Client disconnected or the server is tearing the task down.
        reason = "client_closed"
        raise
    except Exception:
        reason = "error"
        logger.warning("front-stream connection error", exc_info=True)
    finally:
        # Suppress errors (and the CancelledError raised into the awaited
        # reader during teardown) so one failing cleanup step can't skip the
        # rest of the finally - the cap DECR and gauge must always run.
        if reader is not None:
            reader.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await reader
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await pubsub.aclose()
        with contextlib.suppress(Exception):
            await r.decr(connection_count_key(account_key))
        realtime_connections_active.dec()
        realtime_connection_duration_seconds.observe(
            time.monotonic() - started
        )
        realtime_connections_closed_total.labels(reason=reason).inc()


@router.get("/stream")
async def stream_fronts(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Open a Server-Sent Events stream of this account's front changes.

    Sends an `event: snapshot` with the current fronting state, then
    `event: front_change` deltas as they happen, with SSE comment heartbeats
    in between. Behind `FRONT_STREAM_ENABLED` (404 when off) and a per-account
    connection cap (429 when exceeded).
    """
    if not settings.front_stream_enabled:
        realtime_handshake_failures_total.labels(reason="disabled").inc()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
        )

    _ensure_fronts_read_scope(request)

    system_ids = await authorized_front_system_ids(user, db)
    account_key = str(user.id)
    auth_ctx = _auth_context(request)

    # Connection cap: INCR-then-check on the account counter. DECR happens in
    # the generator's finally (below) on every exit path; a rejected handshake
    # DECRs here immediately since no generator is started.
    r = await get_redis()
    cap = settings.front_stream_max_connections_per_account
    conns_key = connection_count_key(account_key)
    count = await r.incr(conns_key)
    await r.expire(conns_key, max(settings.front_stream_heartbeat_seconds * 5, 60))
    if cap > 0 and count > cap:
        await r.decr(conns_key)
        realtime_handshake_failures_total.labels(reason="connection_cap").inc()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many concurrent front-stream connections for this account",
        )

    return StreamingResponse(
        _stream(account_key, system_ids, auth_ctx),
        media_type="text/event-stream",
        headers={
            # Defeat proxy buffering / caching that would defeat a live stream.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

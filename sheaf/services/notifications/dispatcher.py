"""Outbox dispatcher: claims due rows, resolves recipients, dispatches.

Runs as a long-lived asyncio task started in `lifespan()` (see
`sheaf/main.py`). Each tick:

1. SELECT FOR UPDATE SKIP LOCKED a batch of due, undelivered rows.
2. For each: load the channel + watch token; skip+drop if revoked or
   destination_state != 'active'.
3. Re-resolve the watched member's visibility against the current channel
   config (resolution at dispatch, not enqueue).
4. Apply debounce + quiet hours; requeue if not currently deliverable.
5. Render payload + dispatch via per-type handler.
6. Mark delivered / increment failure count with backoff.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.config import settings
from sheaf.database import async_session_factory
from sheaf.models.member import Member
from sheaf.models.notification_channel import (
    DestinationState,
    DestinationType,
    NotificationChannel,
)
from sheaf.models.notification_outbox import NotificationOutboxRow
from sheaf.services.members import member_name_plaintext
from sheaf.services.notifications.handlers import deliver
from sheaf.services.notifications.resolution import resolve_member_visibility

logger = logging.getLogger("sheaf.notifications.dispatcher")

_BATCH_SIZE = 32
_BACKOFF_BASE_SECONDS = 30


def _semaphores() -> dict[str, asyncio.Semaphore]:
    return {
        DestinationType.WEB_PUSH.value: asyncio.Semaphore(
            settings.notifications_concurrency_web_push
        ),
        DestinationType.WEBHOOK.value: asyncio.Semaphore(
            settings.notifications_concurrency_webhook
        ),
        DestinationType.NTFY.value: asyncio.Semaphore(
            settings.notifications_concurrency_ntfy
        ),
        DestinationType.PUSHOVER.value: asyncio.Semaphore(
            settings.notifications_concurrency_pushover
        ),
    }


async def dispatcher_loop(stop_event: asyncio.Event | None = None) -> None:
    """Main dispatcher loop. Cancel by setting `stop_event` (or by cancelling
    the task)."""
    sems = _semaphores()
    interval = max(1, settings.notifications_dispatch_interval_seconds)
    logger.info("Notification dispatcher started (interval=%ds)", interval)
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            await _tick(sems)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Dispatcher tick failed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


async def _tick(sems: dict[str, asyncio.Semaphore]) -> None:
    worker_id = f"dispatcher-{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as db:
        rows = await _claim_batch(db, worker_id=worker_id)
        if not rows:
            return
        # Process rows concurrently, bounded by per-destination semaphores.
        tasks = [_process_row(row.id, sems) for row in rows]
        await asyncio.gather(*tasks, return_exceptions=True)


async def _claim_batch(
    db: AsyncSession, *, worker_id: str
) -> list[NotificationOutboxRow]:
    now = datetime.now(UTC)
    # Use FOR UPDATE SKIP LOCKED to claim rows without blocking other workers.
    stmt = (
        select(NotificationOutboxRow)
        .where(
            NotificationOutboxRow.delivered_at.is_(None),
            NotificationOutboxRow.deliver_after <= now,
            NotificationOutboxRow.claimed_at.is_(None),
        )
        .order_by(NotificationOutboxRow.deliver_after)
        .limit(_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    for row in rows:
        row.claimed_at = now
        row.claimed_by = worker_id
    await db.commit()
    return rows


async def _process_row(
    row_id: uuid.UUID, sems: dict[str, asyncio.Semaphore]
) -> None:
    async with async_session_factory() as db:
        row = await _load_row(db, row_id)
        if row is None:
            return

        channel = await _load_channel(db, row.channel_id)
        if channel is None or channel.watch_token.revoked_at is not None:
            await _drop(db, row, "channel revoked or missing")
            return
        if channel.destination_state != DestinationState.ACTIVE.value:
            await _drop(db, row, f"channel state {channel.destination_state}")
            return

        # Debounce: skip if the channel got a successful delivery within the
        # debounce window. Re-queue past the window.
        now = datetime.now(UTC)
        if (
            channel.last_delivered_at is not None
            and channel.debounce_seconds > 0
            and (now - channel.last_delivered_at).total_seconds()
            < channel.debounce_seconds
        ):
            requeue_at = channel.last_delivered_at + timedelta(
                seconds=channel.debounce_seconds
            )
            await _requeue(db, row, requeue_at, reason="debounce")
            return

        # Quiet hours: requeue to window-end if currently inside.
        qh_end = _quiet_hours_end(channel.quiet_hours, now)
        if qh_end is not None:
            await _requeue(db, row, qh_end, reason="quiet_hours")
            return

        # Legacy per-delta payloads (rows enqueued before the aggregated
        # rewrite) are unrenderable here; drop them rather than half-render.
        # Rows are short-lived so this only matters during a deploy window.
        payload = row.event_payload
        if "kind" in payload or "fronting_before" not in payload:
            await _drop(db, row, "legacy payload format dropped on upgrade")
            return

        # Resolve every member in the before/after sets so the renderer can
        # decide what to name and what to redact, per the channel's filter
        # config and cofront_redaction policy.
        member_ids: set[uuid.UUID] = set()
        for s in payload.get("fronting_before", []):
            member_ids.add(uuid.UUID(s))
        for s in payload.get("fronting_after", []):
            member_ids.add(uuid.UUID(s))
        if not member_ids:
            await _drop(db, row, "empty fronting set")
            return

        member_names, visible_set = await _resolve_members(
            db, channel, member_ids
        )

        from sheaf.services.notifications.payload import render_message as _render

        message = _render(
            channel,
            payload=payload,
            member_names=member_names,
            visible_member_ids=visible_set,
        )
        if message.suppress:
            await _drop(db, row, "no visible content for this channel")
            return

        sem = sems.get(channel.destination_type)
        if sem is None:
            await _drop(db, row, f"no handler for {channel.destination_type}")
            return

        async with sem:
            outcome = await deliver(
                channel, message, event_id=str(row.event_id)
            )

        if outcome.ok:
            row.delivered_at = datetime.now(UTC)
            channel.last_delivered_at = row.delivered_at
            await db.commit()
            return

        if outcome.permanent:
            channel.destination_state = DestinationState.DISABLED.value
            await _drop(db, row, f"permanent: {outcome.error}")
            return

        # Transient: backoff + requeue.
        row.failed_attempts += 1
        row.last_error = outcome.error
        backoff = _BACKOFF_BASE_SECONDS * (2 ** min(row.failed_attempts - 1, 6))
        row.next_retry_after = datetime.now(UTC) + timedelta(seconds=backoff)
        row.deliver_after = row.next_retry_after
        row.claimed_at = None
        row.claimed_by = None
        await db.commit()


async def _load_row(
    db: AsyncSession, row_id: uuid.UUID
) -> NotificationOutboxRow | None:
    return await db.get(NotificationOutboxRow, row_id)


async def _load_channel(
    db: AsyncSession, channel_id: uuid.UUID
) -> NotificationChannel | None:
    result = await db.execute(
        select(NotificationChannel)
        .where(NotificationChannel.id == channel_id)
        .options(
            selectinload(NotificationChannel.watch_token),
            selectinload(NotificationChannel.group_rules),
            selectinload(NotificationChannel.member_rules),
        )
    )
    return result.scalar_one_or_none()


async def _resolve_members(
    db: AsyncSession,
    channel: NotificationChannel,
    member_ids: set[uuid.UUID],
) -> tuple[dict[uuid.UUID, str], set[uuid.UUID]]:
    """Bulk-load members with their groups, then per-member resolve visibility.

    Returns `(name_by_id, visible_id_set)`. The watched member is always
    included in the visible set if reachable here (caller already checked).
    """
    if not member_ids:
        return {}, set()
    result = await db.execute(
        select(Member)
        .where(Member.id.in_(member_ids))
        .options(selectinload(Member.groups))
    )
    members = list(result.scalars().all())
    names = {
        m.id: (m.display_name or member_name_plaintext(m)) for m in members
    }
    visible: set[uuid.UUID] = set()
    for m in members:
        r = resolve_member_visibility(channel, m, [g.id for g in m.groups])
        if r.included:
            visible.add(m.id)
    return names, visible


async def _requeue(
    db: AsyncSession,
    row: NotificationOutboxRow,
    deliver_after: datetime,
    *,
    reason: str,
) -> None:
    row.deliver_after = deliver_after
    row.claimed_at = None
    row.claimed_by = None
    row.last_error = f"requeued: {reason}"
    await db.commit()


async def _drop(
    db: AsyncSession, row: NotificationOutboxRow, reason: str
) -> None:
    """Mark a row 'delivered' with no actual delivery (filtered out, revoked,
    permanent failure). delivered_at is the natural sentinel for done."""
    row.delivered_at = datetime.now(UTC)
    row.last_error = reason
    await db.commit()


def _quiet_hours_end(quiet_hours: dict | None, now: datetime) -> datetime | None:
    """If `now` is inside the channel's quiet-hours window, return the next
    timestamp at which dispatch is allowed. Else None.

    Window format: `{"start": "22:00", "end": "07:00", "tz": "UTC"}`.
    Crosses-midnight is allowed (start > end). Timezone defaults to UTC.
    """
    if not quiet_hours:
        return None
    start_s = quiet_hours.get("start")
    end_s = quiet_hours.get("end")
    if not start_s or not end_s:
        return None
    try:
        start_t = time.fromisoformat(start_s)
        end_t = time.fromisoformat(end_s)
    except ValueError:
        return None

    # All comparisons in UTC for v1.
    now_utc = now.astimezone(UTC)
    today = now_utc.date()
    start_dt = datetime.combine(today, start_t, tzinfo=UTC)
    end_dt = datetime.combine(today, end_t, tzinfo=UTC)

    if start_t <= end_t:
        # Same-day window.
        if start_dt <= now_utc < end_dt:
            return end_dt
        return None

    # Crosses midnight: e.g. 22:00 → 07:00 means [22:00, 24:00) U [00:00, 07:00).
    if now_utc >= start_dt:
        # In the late-evening half; window ends tomorrow at end_t.
        return end_dt + timedelta(days=1)
    if now_utc < end_dt:
        # In the early-morning half; window ends today at end_t.
        return end_dt
    return None

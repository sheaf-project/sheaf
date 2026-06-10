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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from sheaf.observability.metrics import (
    notifications_dispatch_duration_seconds,
    notifications_dispatch_lag_seconds,
    notifications_dispatched_total,
)
from sheaf.services.members import member_name_plaintext
from sheaf.services.notifications.handlers import deliver
from sheaf.services.notifications.payload import RenderedMessage
from sheaf.services.notifications.resolution import resolve_member_visibility

logger = logging.getLogger("sheaf.notifications.dispatcher")

_BATCH_SIZE = 32
_BACKOFF_BASE_SECONDS = 30


def _semaphores() -> dict[str, asyncio.Semaphore]:
    # mobile_push fans out per-token to FCM and APNs (dev or prod, per
    # token); the per-device dispatch happens inside the handler under
    # the dedicated FCM / APNs sub-semaphores so per-provider concurrency
    # budgets still apply. The outer channel-level semaphore is sized
    # generously since the real work is delegated.
    apns_sem = asyncio.Semaphore(settings.notifications_concurrency_apns)
    fcm_sem = asyncio.Semaphore(settings.notifications_concurrency_fcm)
    mobile_sem = asyncio.Semaphore(
        settings.notifications_concurrency_apns
        + settings.notifications_concurrency_fcm
    )
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
        DestinationType.MOBILE_PUSH.value: mobile_sem,
        # Legacy entries retained for read-back / replay of any pre-
        # migration outbox rows still in flight at upgrade time. New
        # channels never land here.
        DestinationType.FCM.value: fcm_sem,
        DestinationType.APNS_DEV.value: apns_sem,
        DestinationType.APNS_PROD.value: apns_sem,
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
    # Claims are leases, not tombstones. A worker that died between
    # claiming and delivering (crash, deploy, OOM) used to strand its
    # batch forever: the re-claim filter demanded claimed_at IS NULL and
    # the retention sweep only deletes delivered rows, so every unclean
    # restart silently dropped up to a batch of notifications. Rows whose
    # claim is older than the lease are presumed orphaned and re-claimed.
    # Lease-based (rather than reset-on-startup) so it stays correct with
    # multiple replicas: another process's live claim is never touched.
    lease_cutoff = now - timedelta(
        minutes=settings.notifications_claim_lease_minutes
    )
    stmt = (
        select(NotificationOutboxRow)
        .where(
            NotificationOutboxRow.delivered_at.is_(None),
            NotificationOutboxRow.deliver_after <= now,
            (
                NotificationOutboxRow.claimed_at.is_(None)
                | (NotificationOutboxRow.claimed_at < lease_cutoff)
            ),
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
            # channel_type may be unknown here (channel could be None)
            ct = channel.destination_type if channel is not None else None
            await _drop(
                db, row, "channel revoked or missing",
                channel_type=ct, outcome="revoked",
            )
            return
        if channel.destination_state != DestinationState.ACTIVE.value:
            await _drop(
                db, row, f"channel state {channel.destination_state}",
                channel_type=channel.destination_type, outcome="revoked",
            )
            return

        # Reminders take a separate code path: no member resolution, no
        # filter application, no debounce/quiet-hours suppression. They
        # were scheduled at a specific time on purpose; if the user
        # didn't want them firing during quiet hours they wouldn't have
        # set them. Per-channel rate limits still apply via the semaphore.
        now = datetime.now(UTC)
        if row.event_type == "reminder":
            message = _render_reminder(row.event_payload)
            await _deliver_or_retry(db, row, channel, message, sems)
            return

        # Debounce: skip if the channel got a successful delivery within the
        # debounce window. Re-queue past the window.
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
            await _drop(
                db, row, "legacy payload format dropped on upgrade",
                channel_type=channel.destination_type, outcome="dropped",
            )
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
            await _drop(
                db, row, "empty fronting set",
                channel_type=channel.destination_type, outcome="filtered",
            )
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
            await _drop(
                db, row, "no visible content for this channel",
                channel_type=channel.destination_type, outcome="filtered",
            )
            return

        await _deliver_or_retry(db, row, channel, message, sems)


def _render_reminder(payload: dict) -> RenderedMessage:
    """Build a delivery message for a reminder outbox row.

    Two payload kinds:
      - reminder_single: one fire of one reminder; title is the user's
        reminder title, body is the user's body verbatim.
      - reminder_digest: multiple missed firings of one reminder, drained
        because a scope-member just started fronting. Body summarises with
        a count + last-missed timestamp; recipients click through if they
        want the full detail.
    """
    kind = payload.get("kind")
    title = payload.get("title") or "Reminder"
    body = payload.get("body") or ""
    if kind == "reminder_digest":
        count = payload.get("missed_count") or 0
        last = payload.get("last_missed_at") or ""
        suffix = f" (×{count})" if count > 1 else ""
        # Keep the body terse — channels with a strict size budget (web push
        # at ~4KB) don't have room for full per-occurrence detail anyway.
        digest_lines = [f"{title}{suffix}"]
        if last:
            digest_lines.append(f"last scheduled at {last}")
        if body:
            digest_lines.append(body)
        return RenderedMessage(
            title="Missed reminders" if count > 1 else title,
            body="\n".join(digest_lines),
        )
    return RenderedMessage(title=title, body=body)


async def _deliver_or_retry(
    db: AsyncSession,
    row: NotificationOutboxRow,
    channel: NotificationChannel,
    message: RenderedMessage,
    sems: dict[str, asyncio.Semaphore],
) -> None:
    """Common delivery tail used by both front-change and reminder rows."""
    sem = sems.get(channel.destination_type)
    if sem is None:
        await _drop(
            db, row, f"no handler for {channel.destination_type}",
            channel_type=channel.destination_type, outcome="dropped",
        )
        return

    owner_user_id, owner_tier = await _resolve_channel_owner(db, channel)

    ct = channel.destination_type
    with notifications_dispatch_duration_seconds.labels(channel_type=ct).time():
        async with sem:
            outcome = await deliver(
                channel,
                message,
                event_id=str(row.event_id),
                owner_user_id=owner_user_id,
                owner_tier=owner_tier,
                db=db,
            )

    if outcome.ok:
        row.delivered_at = datetime.now(UTC)
        channel.last_delivered_at = row.delivered_at
        await db.commit()
        notifications_dispatched_total.labels(
            channel_type=ct, outcome="success",
        ).inc()
        lag = (row.delivered_at - row.enqueued_at).total_seconds()
        notifications_dispatch_lag_seconds.labels(channel_type=ct).observe(
            max(lag, 0)
        )
        return

    if outcome.permanent:
        channel.destination_state = DestinationState.DISABLED.value
        await _drop(
            db, row, f"permanent: {outcome.error}",
            channel_type=ct, outcome="permanent_failure",
        )
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
    notifications_dispatched_total.labels(
        channel_type=ct, outcome="transient_failure",
    ).inc()


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


async def _resolve_channel_owner(
    db: AsyncSession, channel: NotificationChannel
) -> tuple[uuid.UUID | None, str | None]:
    """Walk channel -> watch_token -> system -> user to surface (user_id,
    tier) for handlers that enforce per-user quotas. Returns (None, None)
    if any link is missing — handlers should treat that as "skip the
    per-user check, fall back to deployment cap only"."""
    from sheaf.models.system import System
    from sheaf.models.user import User

    if channel.watch_token is None:
        return None, None
    result = await db.execute(
        select(User.id, User.tier)
        .join(System, System.user_id == User.id)
        .where(System.id == channel.watch_token.system_id)
    )
    row = result.first()
    if row is None:
        return None, None
    return row.id, row.tier


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
    db: AsyncSession,
    row: NotificationOutboxRow,
    reason: str,
    *,
    channel_type: str | None = None,
    outcome: str = "dropped",
) -> None:
    """Mark a row 'delivered' with no actual delivery (filtered out, revoked,
    permanent failure). delivered_at is the natural sentinel for done.

    `channel_type` and `outcome` feed the dispatched-total metric. When
    channel_type is None we skip the bump — happens only when the channel
    row itself couldn't be loaded.
    """
    row.delivered_at = datetime.now(UTC)
    row.last_error = reason
    await db.commit()
    if channel_type:
        notifications_dispatched_total.labels(
            channel_type=channel_type, outcome=outcome,
        ).inc()


def _quiet_hours_end(quiet_hours: dict | None, now: datetime) -> datetime | None:
    """If `now` is inside the channel's quiet-hours window, return the next
    UTC timestamp at which dispatch is allowed. Else None.

    Window format: `{"start": "22:00", "end": "07:00", "tz": "Europe/Berlin"}`.
    Comparisons happen in the channel's tz so DST shifts move the window
    correctly (a "10 PM to 7 AM Berlin" channel keeps doing the right
    thing across the spring/autumn changeovers). Returned timestamp is
    converted back to UTC for the outbox row's deliver_after column.
    Crosses-midnight is allowed (start > end).
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

    tz_name = quiet_hours.get("tz") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        # Bad tz somehow stored despite the schema validator (or older row
        # written before validation existed). Don't crash dispatch — fall
        # back to UTC so we still respect SOME window boundary.
        logger.warning("quiet_hours has unknown tz %r; falling back to UTC", tz_name)
        tz = UTC

    now_local = now.astimezone(tz)
    today = now_local.date()
    start_dt = datetime.combine(today, start_t, tzinfo=tz)
    end_dt = datetime.combine(today, end_t, tzinfo=tz)

    if start_t <= end_t:
        # Same-day window.
        if start_dt <= now_local < end_dt:
            return end_dt.astimezone(UTC)
        return None

    # Crosses midnight: e.g. 22:00 -> 07:00 means [22:00, 24:00) U [00:00, 07:00).
    if now_local >= start_dt:
        # In the late-evening half; window ends tomorrow at end_t.
        return (end_dt + timedelta(days=1)).astimezone(UTC)
    if now_local < end_dt:
        # In the early-morning half; window ends today at end_t.
        return end_dt.astimezone(UTC)
    return None

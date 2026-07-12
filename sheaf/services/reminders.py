"""Reminder scheduling and dispatch.

Two trigger types share one Reminder row:

- automated: enqueued reactively from `emit_front_change` when a matching
  event lands. The notification outbox carries the delay via
  `deliver_after`. No scheduler tick involved.
- repeated: a 60-second job tick walks enabled rows and fires those whose
  next-fire time is in the past. Member-scoped reminders that fire while
  no scoped member is fronting drop into a pending queue for digesting on
  the next scope-member front-start.

Reminder payloads ride the existing `notification_outbox` with
`event_type="reminder"`. The dispatcher branches on event_type and skips
the front-change-specific resolution / filter / cofront-redaction path
for these — reminders are direct sends to the channel with the literal
title and body the user configured.

# Future enhancements (not in v1)

- Mobile push notifications (APNs / FCM) as a destination type. Today
  reminders ride the existing notification channels: web push, webhook,
  ntfy, Pushover. For most users on mobile, the natural place for a
  reminder is the OS push system on their own phone — that needs an
  APNs/FCM destination type alongside the existing four, plus a
  capability flow for the mobile apps to register their device tokens.
  Likely the highest-impact follow-up; reminders are the feature most
  users would expect to fire to their phone first.
- Member-scoping condition on automated timers ("ping me 30 min after
  Alice fronts, but only if Bob isn't fronting").
- Per-channel-type custom digest template (right now the digest format
  is hardcoded to "title (×count) — last <timestamp>").
- Reminder pause/snooze without disabling (e.g. "skip the next two
  fires" without a full edit dance).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.crypto import decrypt, encrypt
from sheaf.models.front import Front
from sheaf.models.notification_channel import (
    DestinationState,
    NotificationChannel,
)
from sheaf.models.notification_outbox import NotificationOutboxRow
from sheaf.models.reminder import Reminder, ReminderPending

logger = logging.getLogger("sheaf.reminders")

# Cap on the pending queue per reminder. Anything beyond this drops the
# oldest entry. Keeps the digest notification readable when a scope
# member has been gone for a long stretch.
_MAX_PENDING_PER_REMINDER = 5

# Bitmask: Mon=1, Tue=2, Wed=4, Thu=8, Fri=16, Sat=32, Sun=64.
# Matches Python's datetime.weekday() ordering (Mon=0..Sun=6).
_DOW_BITS = [1 << i for i in range(7)]


# --- Helpers ---------------------------------------------------------------


def _decrypt_or_none(value: str | None) -> str | None:
    return decrypt(value) if value else None


def _zone(name: str | None) -> ZoneInfo:
    """Resolve a tz name to ZoneInfo, falling back to UTC if invalid.

    Validation also happens at the API layer; this is the safety net for
    rows that pre-date a deprecation, or for misconfigurations that
    shouldn't crash the scheduler tick on every iteration.
    """
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("reminder has unknown timezone %s, using UTC", name)
        return ZoneInfo("UTC")


def compute_next_fire(
    reminder: Reminder, *, after: datetime | None = None
) -> datetime | None:
    """Compute the next fire time strictly after `after` (default: now).

    Returns None for automated reminders (they're event-driven, not
    schedule-driven) or for repeated reminders without a valid schedule
    config.
    """
    if reminder.trigger_type != "repeated":
        return None
    after = after or datetime.now(UTC)
    tz = _zone(reminder.schedule_tz)

    # Advanced cron mode wins when present.
    if reminder.cron_expression:
        try:
            it = croniter(reminder.cron_expression, after.astimezone(tz))
            local_next = it.get_next(datetime)
            if local_next.tzinfo is None:
                local_next = local_next.replace(tzinfo=tz)
            return local_next.astimezone(UTC)
        except (ValueError, KeyError):
            logger.warning(
                "reminder %s has invalid cron expression %r, skipping",
                reminder.id,
                reminder.cron_expression,
            )
            return None

    if not reminder.schedule_kind or not reminder.schedule_time:
        return None

    hh, mm = reminder.schedule_time.split(":")
    hour, minute = int(hh), int(mm)

    cursor_local = after.astimezone(tz)
    # Probe a generous number of candidate days. 366 covers any monthly
    # schedule on the day of a non-existent month-day (Feb 30 etc.).
    for offset in range(366):
        candidate = (cursor_local + timedelta(days=offset)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= cursor_local:
            continue
        if reminder.schedule_kind == "daily":
            return candidate.astimezone(UTC)
        if reminder.schedule_kind == "weekly":
            mask = reminder.schedule_dow_mask or 0
            if mask == 0:
                return None
            if mask & _DOW_BITS[candidate.weekday()]:
                return candidate.astimezone(UTC)
        elif reminder.schedule_kind == "monthly":
            dom = reminder.schedule_dom or 1
            if candidate.day == dom:
                return candidate.astimezone(UTC)
    return None


# --- Enqueue helpers -------------------------------------------------------


def _new_outbox_row(
    *,
    channel_id: uuid.UUID,
    deliver_after: datetime,
    event_payload: dict,
) -> NotificationOutboxRow:
    """Construct a not-yet-flushed outbox row for a reminder fire.

    Keeping the outbox shape consistent with front-change events lets the
    same dispatcher claim and process both. The dispatcher branches on
    event_type to render a reminder body vs a front-change body.
    """
    now = datetime.now(UTC)
    return NotificationOutboxRow(
        id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        channel_id=channel_id,
        event_type="reminder",
        event_payload=event_payload,
        enqueued_at=now,
        deliver_after=deliver_after,
    )


def _reminder_payload(
    reminder: Reminder, *, scheduled_for: datetime | None = None
) -> dict:
    """Render the outbox event_payload for a single reminder fire."""
    return {
        "kind": "reminder_single",
        "reminder_id": str(reminder.id),
        "title": _decrypt_or_none(reminder.title) or "",
        "body": _decrypt_or_none(reminder.body),
        "scheduled_for": (
            scheduled_for.astimezone(UTC).isoformat()
            if scheduled_for is not None
            else None
        ),
    }


def _digest_payload(
    reminder: Reminder, *, pending_rows: Iterable[ReminderPending]
) -> dict:
    """Render a digest event_payload covering missed firings of one reminder.

    Pooling across *reminders* (e.g. "Daily meds + Stretch break missed
    while Bob was away") happens at dispatch time inside the channel
    handler — at this point we still produce one outbox row per reminder
    so each can be tracked independently.
    """
    rows = sorted(pending_rows, key=lambda r: r.scheduled_for)
    # A reminder's times belong in its own schedule timezone (the zone the user
    # set the schedule in), so the digest reads back the way it was configured.
    # `..._at` stays UTC ISO for any structured consumer; `last_missed_display`
    # is the human-facing form the notification body renders, with a stamp.
    zone = _zone(reminder.schedule_tz)
    last_display = (
        rows[-1].scheduled_for.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")
        if rows
        else None
    )
    return {
        "kind": "reminder_digest",
        "reminder_id": str(reminder.id),
        "title": _decrypt_or_none(reminder.title) or "",
        "body": _decrypt_or_none(reminder.body),
        "missed_count": len(rows),
        "first_missed_at": rows[0].scheduled_for.astimezone(UTC).isoformat()
        if rows
        else None,
        "last_missed_at": rows[-1].scheduled_for.astimezone(UTC).isoformat()
        if rows
        else None,
        "last_missed_display": last_display,
    }


# --- Repeated-reminder scheduler tick -------------------------------------


async def tick_repeated_reminders(db: AsyncSession) -> int:
    """Fire any due repeated reminders.

    Returns the number of outbox rows enqueued (excluding pending-queue
    inserts). Called from the job runner on a 60-second cadence.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        select(Reminder)
        .options(
            selectinload(Reminder.scope_members),
            selectinload(Reminder.pending),
        )
        .where(
            Reminder.trigger_type == "repeated",
            Reminder.enabled.is_(True),
        )
    )
    reminders = list(result.scalars().all())
    enqueued = 0

    # Batch-load currently-fronting member ids per system so we can answer
    # "is any scope-member of this reminder fronting?" without one query
    # per reminder. Most deployments have one system per user, so this is
    # usually a single small set.
    system_ids = {r.system_id for r in reminders}
    fronting_by_system = await _currently_fronting_by_system(db, system_ids)
    # Batch the channel lookups too — one query instead of one per reminder.
    channels_by_id = await _load_channels(db, {r.channel_id for r in reminders})

    for reminder in reminders:
        if not _channel_is_active(channels_by_id.get(reminder.channel_id)):
            continue

        # Anchor scheduling against last_fired_at (or created_at if never
        # fired) so a server-down period doesn't stack up multiple fires.
        anchor = reminder.last_fired_at or reminder.created_at
        next_fire = compute_next_fire(reminder, after=anchor)
        if next_fire is None or next_fire > now:
            continue

        fronting = fronting_by_system.get(reminder.system_id, set())
        scoped_active = _scope_satisfied(reminder, fronting)
        if reminder.scope == "system" or scoped_active:
            db.add(
                _new_outbox_row(
                    channel_id=reminder.channel_id,
                    deliver_after=next_fire,
                    event_payload=_reminder_payload(reminder, scheduled_for=next_fire),
                )
            )
            enqueued += 1
        elif reminder.digest_when_absent:
            await _enqueue_pending(db, reminder, scheduled_for=next_fire)

        reminder.last_fired_at = next_fire

    return enqueued


async def _enqueue_pending(
    db: AsyncSession, reminder: Reminder, *, scheduled_for: datetime
) -> None:
    """Push a missed-firing onto the per-reminder pending queue.

    The cap is enforced by deleting the oldest extra rows. We do this in
    one transaction with the insert so the visible queue length never
    exceeds the cap.
    """
    db.add(
        ReminderPending(
            id=uuid.uuid4(),
            reminder_id=reminder.id,
            scheduled_for=scheduled_for,
        )
    )
    await db.flush()

    # Trim to cap. There's a tiny race here if multiple ticks ran in
    # parallel, but the scheduler is single-process; in practice this is
    # always exact.
    pending_q = await db.execute(
        select(ReminderPending)
        .where(ReminderPending.reminder_id == reminder.id)
        .order_by(ReminderPending.scheduled_for.desc())
    )
    rows = list(pending_q.scalars().all())
    if len(rows) > _MAX_PENDING_PER_REMINDER:
        for old in rows[_MAX_PENDING_PER_REMINDER:]:
            await db.delete(old)


# --- Automated-trigger hook ------------------------------------------------


async def emit_for_front_event(
    db: AsyncSession,
    *,
    system_id: uuid.UUID,
    started_member_ids: set[uuid.UUID],
    stopped_member_ids: set[uuid.UUID],
) -> int:
    """Wire automated reminders into front-change events.

    For every enabled `automated` reminder in this system whose trigger
    matches one of the started/stopped member ids (or "any"), enqueue an
    outbox row with `deliver_after = now + delay_seconds`. Returns the
    number of rows enqueued.
    """
    if not (started_member_ids or stopped_member_ids):
        return 0

    result = await db.execute(
        select(Reminder).where(
            Reminder.system_id == system_id,
            Reminder.trigger_type == "automated",
            Reminder.enabled.is_(True),
        )
    )
    reminders = list(result.scalars().all())
    if not reminders:
        return 0

    now = datetime.now(UTC)
    enqueued = 0
    for reminder in reminders:
        channel = await _load_channel(db, reminder.channel_id)
        if not _channel_is_active(channel):
            continue

        if not _trigger_matches(
            reminder,
            started_member_ids=started_member_ids,
            stopped_member_ids=stopped_member_ids,
        ):
            continue

        delay = max(0, reminder.delay_seconds or 0)
        deliver_after = now + timedelta(seconds=delay)
        db.add(
            _new_outbox_row(
                channel_id=reminder.channel_id,
                deliver_after=deliver_after,
                event_payload=_reminder_payload(reminder),
            )
        )
        reminder.last_fired_at = now
        enqueued += 1
    return enqueued


# --- Drain hook (member-scoped digest) -------------------------------------


async def drain_digests_for_started_members(
    db: AsyncSession,
    *,
    system_id: uuid.UUID,
    started_member_ids: set[uuid.UUID],
    previously_fronting: set[uuid.UUID],
) -> int:
    """When a scope-member starts fronting, drain their reminder pending queue.

    Only fires when the started member is the FIRST scope-member of a
    reminder to come on (i.e., before this event, no scope-member of this
    reminder was fronting). Otherwise an existing scope-member is already
    receiving notifications and the digest is redundant.

    Returns the number of digest outbox rows enqueued.
    """
    if not started_member_ids:
        return 0

    # Pull every reminder in this system that has at least one pending row,
    # plus its scope members and channel.
    result = await db.execute(
        select(Reminder)
        .options(
            selectinload(Reminder.scope_members),
            selectinload(Reminder.pending),
        )
        .where(
            Reminder.system_id == system_id,
            Reminder.scope == "member",
        )
    )
    reminders = [r for r in result.scalars().all() if r.pending]
    if not reminders:
        return 0

    enqueued = 0
    now = datetime.now(UTC)
    for reminder in reminders:
        scope_ids = {m.id for m in reminder.scope_members}
        if not scope_ids:
            continue

        # Was a scope-member already fronting before this event? If so,
        # the digest already had its chance and shouldn't fire again.
        if scope_ids & previously_fronting:
            continue
        # Is at least one scope-member among the just-started?
        if not (scope_ids & started_member_ids):
            continue

        channel = await _load_channel(db, reminder.channel_id)
        if not _channel_is_active(channel):
            continue

        db.add(
            _new_outbox_row(
                channel_id=reminder.channel_id,
                deliver_after=now,
                event_payload=_digest_payload(reminder, pending_rows=reminder.pending),
            )
        )
        enqueued += 1

        # Empty the queue for this reminder.
        await db.execute(
            delete(ReminderPending).where(
                ReminderPending.reminder_id == reminder.id
            )
        )

    return enqueued


# --- Internal helpers ------------------------------------------------------


def _trigger_matches(
    reminder: Reminder,
    *,
    started_member_ids: set[uuid.UUID],
    stopped_member_ids: set[uuid.UUID],
) -> bool:
    """Decide whether an automated reminder fires for this front-change.

    The trigger event filter ("start", "stop", "any") narrows which side
    of the transition we look at; trigger_member_id then narrows further
    to a specific member, or null = "any member moved on the matching
    side". Result is the OR of (start-matches) and (stop-matches).
    """
    event = reminder.trigger_event
    target = reminder.trigger_member_id

    def _hits(side: set[uuid.UUID]) -> bool:
        if not side:
            return False
        return target is None or target in side

    if event in (None, "any", "start") and _hits(started_member_ids):
        return True
    return event in ("any", "stop") and _hits(stopped_member_ids)


def _scope_satisfied(reminder: Reminder, fronting_ids: set[uuid.UUID]) -> bool:
    """Is the scope condition met right now?

    For system-scoped reminders, always True. For member-scoped, True if
    at least one scope member is currently fronting.
    """
    if reminder.scope != "member":
        return True
    scope_ids = {m.id for m in reminder.scope_members}
    return bool(scope_ids & fronting_ids)


async def _currently_fronting_by_system(
    db: AsyncSession, system_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Map {system_id: {member_ids currently fronting}} for the given systems."""
    system_ids = list(system_ids)
    if not system_ids:
        return {}
    result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(
            Front.system_id.in_(system_ids),
            Front.ended_at.is_(None),
        )
    )
    out: dict[uuid.UUID, set[uuid.UUID]] = {sid: set() for sid in system_ids}
    for front in result.scalars().all():
        out[front.system_id].update(m.id for m in front.members)
    return out


async def _load_channel(
    db: AsyncSession, channel_id: uuid.UUID
) -> NotificationChannel | None:
    return (
        await db.execute(
            select(NotificationChannel).where(NotificationChannel.id == channel_id)
        )
    ).scalar_one_or_none()


async def _load_channels(
    db: AsyncSession, channel_ids: set[uuid.UUID]
) -> dict[uuid.UUID, NotificationChannel]:
    """Batch-load channels by id — avoids a per-reminder query in the
    repeated-reminder tick."""
    if not channel_ids:
        return {}
    rows = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id.in_(channel_ids))
    )
    return {c.id: c for c in rows.scalars()}


def _channel_is_active(channel: NotificationChannel | None) -> bool:
    return (
        channel is not None
        and channel.destination_state == DestinationState.ACTIVE
    )


# --- Encryption helpers (used by API layer) -------------------------------


def encrypt_title_body(title: str, body: str | None) -> tuple[str, str | None]:
    """Encrypt a reminder's title and body for storage."""
    return encrypt(title), (encrypt(body) if body else None)


def decrypt_for_read(reminder: Reminder) -> dict:
    """Build the decrypted, scope-resolved view used by the read API."""
    return {
        "title": _decrypt_or_none(reminder.title) or "",
        "body": _decrypt_or_none(reminder.body),
        "scope_member_ids": [m.id for m in reminder.scope_members],
        "pending_count": len(reminder.pending),
        "next_fire_at": compute_next_fire(reminder),
    }

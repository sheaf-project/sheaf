"""Front-change event emission.

Called from `sheaf/api/v1/fronts.py` inside the same DB transaction as the
mutation. Per-member visibility resolution + payload rendering happen at
dispatch time, not here, so owner config changes between enqueue and
dispatch take effect.

Two distinct kinds of "aggregation" live here, don't confuse them:

  * Per-transition collapse (always on): one state change moving N members
    at once produces ONE outbox row per matching channel, carrying the full
    before/after fronting sets. This is intrinsic to the payload shape.

  * Time-window aggregation (opt-in, `aggregation_window_seconds > 0`):
    several *separate* front changes within a rolling window collapse into
    a single outbox row whose payload holds the NET transition (the fronting
    state at window open -> the latest fronting state). The dispatcher then
    delivers that one row when the window closes, so debounce, quiet hours,
    retry, and rendering all apply to it unchanged. This is what turns a
    "C stopped" + "B started" cofront swap into one notification. See
    `_fold_or_open_window`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.models.notification_channel import (
    DestinationState,
    NotificationChannel,
)
from sheaf.models.notification_outbox import NotificationOutboxRow
from sheaf.models.watch_token import WatchToken


@dataclass(frozen=True, slots=True)
class FrontState:
    """Snapshot of who is currently fronting at a point in time.

    `cofronters_by_member` maps each fronting member to the set of *other*
    members fronting alongside them (used to detect whether any persisted
    member's co-fronter set changed across the transition).
    """

    fronting_member_ids: frozenset[uuid.UUID]
    cofronters_by_member: dict[uuid.UUID, frozenset[uuid.UUID]] = field(
        default_factory=dict
    )


def _has_cofront_change(before: FrontState, after: FrontState) -> bool:
    """True if any member fronting in both states had their co-fronter set
    change. Used to gate `trigger_on_cofront_change` channels at enqueue
    time without naming any specific member."""
    persisted = before.fronting_member_ids & after.fronting_member_ids
    for mid in persisted:
        if before.cofronters_by_member.get(
            mid, frozenset()
        ) != after.cofronters_by_member.get(mid, frozenset()):
            return True
    return False


def make_state(
    open_fronts_with_members: list[tuple[uuid.UUID, list[uuid.UUID]]],
) -> FrontState:
    """Build a FrontState from a list of `(front_id, member_ids)` tuples for
    currently-open fronts. A member can appear in multiple open fronts; we
    union them."""
    fronting: set[uuid.UUID] = set()
    cofronters: dict[uuid.UUID, set[uuid.UUID]] = {}

    for _front_id, member_ids in open_fronts_with_members:
        member_set = set(member_ids)
        fronting |= member_set
        for mid in member_set:
            cofronters.setdefault(mid, set()).update(member_set - {mid})

    return FrontState(
        fronting_member_ids=frozenset(fronting),
        cofronters_by_member={k: frozenset(v) for k, v in cofronters.items()},
    )


async def emit_front_change(
    db: AsyncSession,
    *,
    system_id: uuid.UUID,
    before: FrontState,
    after: FrontState,
    event_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Enqueue this state transition to matching channels.

    A switch with N members moving produces ONE row per channel - the row's
    payload carries the full before/after fronting sets, and the dispatcher
    renders a single aggregated message at delivery time.

    Channels with a time-window aggregation set (`aggregation_window_seconds
    > 0`) instead fold this transition into their currently-open window row
    (or open a fresh one); see `_fold_or_open_window`.

    Returns the number of channels enqueued to (a fold into an open window
    counts, since it's the delivery this transition contributes to). Caller
    commits the session.
    """
    started_ids = after.fronting_member_ids - before.fronting_member_ids
    stopped_ids = before.fronting_member_ids - after.fronting_member_ids
    started = bool(started_ids)
    stopped = bool(stopped_ids)
    cofront_changed = _has_cofront_change(before, after)
    if not (started or stopped or cofront_changed):
        return 0

    # Reminders ride alongside front-change notifications: automated
    # timers fire `delay_seconds` after the matching event, and member-
    # scoped repeated-reminder digests drain when a scope-member starts
    # fronting after a stretch with none of them on. Importing here to
    # avoid a circular import at module load.
    from sheaf.services.reminders import (
        drain_digests_for_started_members,
        emit_for_front_event,
    )

    await emit_for_front_event(
        db,
        system_id=system_id,
        started_member_ids=set(started_ids),
        stopped_member_ids=set(stopped_ids),
    )
    await drain_digests_for_started_members(
        db,
        system_id=system_id,
        started_member_ids=set(started_ids),
        previously_fronting=set(before.fronting_member_ids),
    )

    event_id = event_id or uuid.uuid4()
    now = now or datetime.now(UTC)

    # Load active channels for non-revoked watch tokens of this system.
    result = await db.execute(
        select(NotificationChannel)
        .join(WatchToken, NotificationChannel.watch_token_id == WatchToken.id)
        .where(
            WatchToken.system_id == system_id,
            WatchToken.revoked_at.is_(None),
            NotificationChannel.destination_state == DestinationState.ACTIVE.value,
            NotificationChannel.event_type == "front_change",
        )
        .options(selectinload(NotificationChannel.watch_token))
    )
    channels = list(result.scalars().all())
    if not channels:
        return 0

    before_ids = sorted(str(m) for m in before.fronting_member_ids)
    after_ids = sorted(str(m) for m in after.fronting_member_ids)

    enqueued = 0
    # Sort by id so two concurrent front changes touching the same channels
    # take the per-channel locks (below) in the same order -> no deadlock.
    for channel in sorted(channels, key=lambda c: str(c.id)):
        # Does this transition match any of the channel's enabled triggers?
        # Triggering on start matches if any member started, etc. Channels
        # with no enabled triggers are effectively muted.
        matches_trigger = (
            (channel.trigger_on_start and started)
            or (channel.trigger_on_stop and stopped)
            or (channel.trigger_on_cofront_change and cofront_changed)
        )

        window = channel.aggregation_window_seconds
        if window and window > 0:
            if await _fold_or_open_window(
                db,
                channel,
                now=now,
                window_seconds=window,
                event_id=event_id,
                before_ids=before_ids,
                after_ids=after_ids,
                matches_trigger=matches_trigger,
            ):
                enqueued += 1
            continue

        # No time-window aggregation: one row per matching transition.
        if not matches_trigger:
            continue
        row = NotificationOutboxRow(
            event_id=event_id,
            channel_id=channel.id,
            event_type="front_change",
            event_payload={
                "fronting_before": before_ids,
                "fronting_after": after_ids,
            },
            enqueued_at=now,
            deliver_after=now,
        )
        db.add(row)
        enqueued += 1

    return enqueued


async def _fold_or_open_window(
    db: AsyncSession,
    channel: NotificationChannel,
    *,
    now: datetime,
    window_seconds: int,
    event_id: uuid.UUID,
    before_ids: list[str],
    after_ids: list[str],
    matches_trigger: bool,
) -> bool:
    """Coalesce this front change into the channel's open aggregation window.

    Returns True if a row was created or an open window row was updated.

    Semantics: the window opens at the first triggering change and closes
    `window_seconds` later (deliver_after). Every front change inside it
    (matching a trigger or not) advances the window's `fronting_after` to
    the current state while its `fronting_before` stays pinned at the state
    when the window opened. The stored payload is therefore the NET
    transition over the window; the dispatcher renders it once and applies
    the channel's triggers to that net (so a member who flapped on and off
    within the window nets to nothing, and a "C out, B in" swap nets to one
    "B started / C stopped" message). Non-matching changes must still fold,
    or the net `after` would be stale and name someone who has since left.

    Concurrency: the channel row is locked FOR UPDATE first, so two
    concurrent front changes on the same system can't both decide "no window
    open" and each open one (which would split the batch into two
    deliveries). The lock is released when the caller commits the mutation
    transaction. A row that is already claimed by the dispatcher, awaiting
    retry (`failed_attempts > 0`), or past its window end is never folded
    into - such a change starts a fresh window instead.
    """
    # Serialize find-or-create for this channel against concurrent emitters.
    await db.execute(
        select(NotificationChannel.id)
        .where(NotificationChannel.id == channel.id)
        .with_for_update()
    )

    open_row = (
        await db.execute(
            select(NotificationOutboxRow)
            .where(
                NotificationOutboxRow.channel_id == channel.id,
                NotificationOutboxRow.event_type == "front_change",
                NotificationOutboxRow.delivered_at.is_(None),
                NotificationOutboxRow.claimed_at.is_(None),
                NotificationOutboxRow.failed_attempts == 0,
                NotificationOutboxRow.deliver_after > now,
            )
            .order_by(NotificationOutboxRow.deliver_after.desc())
            .with_for_update()
            .limit(1)
        )
    ).scalar_one_or_none()

    if open_row is not None:
        # Advance the net `after`; keep the window's original `before`.
        # Reassign (not in-place mutate) so SQLAlchemy flags the JSONB dirty.
        open_row.event_payload = {
            "fronting_before": open_row.event_payload.get(
                "fronting_before", before_ids
            ),
            "fronting_after": after_ids,
        }
        return True

    # No open window. Only open one for a change that actually matches a
    # trigger; a channel that doesn't care about this class of change stays
    # silent, exactly as the non-aggregating path does.
    if not matches_trigger:
        return False
    db.add(
        NotificationOutboxRow(
            event_id=event_id,
            channel_id=channel.id,
            event_type="front_change",
            event_payload={
                "fronting_before": before_ids,
                "fronting_after": after_ids,
            },
            enqueued_at=now,
            deliver_after=now + timedelta(seconds=window_seconds),
        )
    )
    return True


async def snapshot_front_state(
    db: AsyncSession, system_id: uuid.UUID
) -> FrontState:
    """Snapshot the current open-fronts state for a system. Use this *before*
    a mutation, then again *after*, then pass both to `emit_front_change`."""
    from sheaf.models.front import Front

    result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(Front.system_id == system_id, Front.ended_at.is_(None))
    )
    fronts = result.scalars().all()
    return make_state([(f.id, [m.id for m in f.members]) for f in fronts])

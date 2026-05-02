"""Front-change event emission.

Called from `sheaf/api/v1/fronts.py` inside the same DB transaction as the
mutation. Aggregates the entire fronting-state transition into one outbox
row per matching channel — even when many members move at once. Per-member
visibility resolution + payload rendering happen at dispatch time, not
here, so owner config changes between enqueue and dispatch take effect.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

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
    """Aggregate the state transition and write outbox rows.

    A switch with N members moving produces ONE row per channel — the row's
    payload carries the full before/after fronting sets, and the dispatcher
    renders a single aggregated message at delivery time.

    Returns the number of outbox rows enqueued. Caller commits the session.
    """
    started = bool(after.fronting_member_ids - before.fronting_member_ids)
    stopped = bool(before.fronting_member_ids - after.fronting_member_ids)
    cofront_changed = _has_cofront_change(before, after)
    if not (started or stopped or cofront_changed):
        return 0

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

    payload = {
        "fronting_before": sorted(str(m) for m in before.fronting_member_ids),
        "fronting_after": sorted(str(m) for m in after.fronting_member_ids),
    }

    enqueued = 0
    for channel in channels:
        # Skip channels whose enabled triggers don't match this transition.
        # Triggering on start matches if any member started, etc. Channels
        # with no enabled triggers are effectively muted.
        if not (
            (channel.trigger_on_start and started)
            or (channel.trigger_on_stop and stopped)
            or (channel.trigger_on_cofront_change and cofront_changed)
        ):
            continue
        row = NotificationOutboxRow(
            event_id=event_id,
            channel_id=channel.id,
            event_type="front_change",
            event_payload=payload,
            enqueued_at=now,
            deliver_after=now,
        )
        db.add(row)
        enqueued += 1

    return enqueued


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

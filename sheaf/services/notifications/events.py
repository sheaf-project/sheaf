"""Front-change event emission.

Called from `sheaf/api/v1/fronts.py` inside the same DB transaction as the
mutation. Computes per-channel deltas (started, stopped, cofront_changes)
and writes one outbox row per (event, channel) for any channel whose
matching `trigger_on_*` is true and whose watch token is not revoked.

Per-member resolution (and payload shaping) happens at dispatch time, not
here, so owner config changes between enqueue and dispatch take effect.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.models.notification_channel import (
    DestinationState,
    NotificationChannel,
)
from sheaf.models.notification_outbox import NotificationOutboxRow
from sheaf.models.watch_token import WatchToken

EventKind = Literal["start", "stop", "cofront_change"]


@dataclass(frozen=True, slots=True)
class FrontState:
    """Snapshot of who is currently fronting at a point in time.

    `cofronters_by_member` maps each fronting member to the set of *other*
    members fronting alongside them (used to detect co-front composition
    changes per watched member).
    """

    fronting_member_ids: frozenset[uuid.UUID]
    cofronters_by_member: dict[uuid.UUID, frozenset[uuid.UUID]] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class _Delta:
    kind: EventKind
    member_id: uuid.UUID
    cofronters_added: frozenset[uuid.UUID] = frozenset()
    cofronters_removed: frozenset[uuid.UUID] = frozenset()


def compute_deltas(before: FrontState, after: FrontState) -> list[_Delta]:
    """Compute the set of (member, event_kind) deltas implied by a transition.

    - `start`: in `after.fronting_member_ids` but not `before`.
    - `stop`: in `before` but not `after`.
    - `cofront_change`: fronting in *both* states, but the set of OTHER
      fronters changed (any add/remove). Reorder alone does not fire.
    """
    deltas: list[_Delta] = []

    started = after.fronting_member_ids - before.fronting_member_ids
    stopped = before.fronting_member_ids - after.fronting_member_ids
    persisted = before.fronting_member_ids & after.fronting_member_ids

    for mid in sorted(started):
        deltas.append(_Delta(kind="start", member_id=mid))

    for mid in sorted(stopped):
        deltas.append(_Delta(kind="stop", member_id=mid))

    for mid in sorted(persisted):
        before_co = before.cofronters_by_member.get(mid, frozenset())
        after_co = after.cofronters_by_member.get(mid, frozenset())
        added = after_co - before_co
        removed = before_co - after_co
        if added or removed:
            deltas.append(
                _Delta(
                    kind="cofront_change",
                    member_id=mid,
                    cofronters_added=frozenset(added),
                    cofronters_removed=frozenset(removed),
                )
            )

    return deltas


def make_state(open_fronts_with_members: list[tuple[uuid.UUID, list[uuid.UUID]]]) -> FrontState:
    """Build a FrontState from a list of `(front_id, member_ids)` tuples for
    currently-open fronts.

    `member_ids` is the list of members in that front. A member can appear
    in multiple open fronts (rare but legal); we union them.
    """
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


def _trigger_field(kind: EventKind) -> str:
    return {
        "start": "trigger_on_start",
        "stop": "trigger_on_stop",
        "cofront_change": "trigger_on_cofront_change",
    }[kind]


async def emit_front_change(
    db: AsyncSession,
    *,
    system_id: uuid.UUID,
    before: FrontState,
    after: FrontState,
    event_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Compute deltas and write outbox rows for any matching active channel.

    Returns the number of outbox rows enqueued. Caller must `commit()` the
    session.
    """
    deltas = compute_deltas(before, after)
    if not deltas:
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

    enqueued = 0
    for delta in deltas:
        trigger_attr = _trigger_field(delta.kind)
        for channel in channels:
            if not getattr(channel, trigger_attr):
                continue
            payload = _build_event_payload(delta, before, after)
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


def _build_event_payload(delta: _Delta, before: FrontState, after: FrontState) -> dict:
    """Pre-resolution event payload. Member names + privacy + filter
    decisions are resolved at dispatch time, not enqueue time."""
    payload: dict = {
        "kind": delta.kind,
        "member_id": str(delta.member_id),
    }
    if delta.kind == "cofront_change":
        payload["cofronters_added"] = sorted(str(m) for m in delta.cofronters_added)
        payload["cofronters_removed"] = sorted(str(m) for m in delta.cofronters_removed)
        payload["cofronters_after"] = sorted(
            str(m) for m in after.cofronters_by_member.get(delta.member_id, frozenset())
        )
    return payload


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

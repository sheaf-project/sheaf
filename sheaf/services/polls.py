"""Polls service layer.

Handles vote casting (with the front-state guard), audit-log writes,
retention helpers, and the cleanup tick that purges expired polls.

The vote-casting layer is the load-bearing piece: it enforces that
the voted-as member is part of the current front, decides whether the
event is a cast / change / withdraw, and atomically writes both the
current-state row and the audit event row.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.config import settings
from sheaf.crypto import decrypt, encrypt
from sheaf.models.activity_event import ActivityAction, ActivityActorType
from sheaf.models.front import Front
from sheaf.models.member import Member
from sheaf.models.poll import (
    Poll,
    PollVote,
    PollVoteAction,
    PollVoteEvent,
)
from sheaf.models.system import System
from sheaf.models.user import User, UserTier
from sheaf.services.activity_log import log_activity

logger = logging.getLogger("sheaf.polls")


# ---------------------------------------------------------------------------
# Tier-aware close-time bounds
# ---------------------------------------------------------------------------


def close_bounds_for_tier(tier: str) -> tuple[int, int]:
    """Return (min_seconds, max_seconds) for the supplied user tier.

    A `max` of 0 means "no upper bound" — the poll runs as long as the
    creator wants. The minimum is shared across all tiers; raising the
    upper bound is the lever we move when we trust capacity.
    """
    min_s = settings.poll_min_close_seconds
    if tier == UserTier.PLUS:
        max_s = settings.poll_max_close_seconds_plus
    elif tier == UserTier.SELF_HOSTED:
        max_s = settings.poll_max_close_seconds_self_hosted
    else:
        max_s = settings.poll_max_close_seconds_free
    return min_s, max_s


def max_retention_days_for_tier(tier: str) -> int:
    """0 = unlimited. Caps the value users can pass as retention_days."""
    if tier == UserTier.PLUS:
        return settings.poll_max_retention_days_plus
    if tier == UserTier.SELF_HOSTED:
        return settings.poll_max_retention_days_self_hosted
    return settings.poll_max_retention_days_free


def max_concurrent_open_for_tier(tier: str) -> int:
    """0 = unlimited. Caps the number of polls a user can have open
    (closes_at in the future) at once on their system."""
    if tier == UserTier.PLUS:
        return settings.poll_max_concurrent_open_plus
    if tier == UserTier.SELF_HOSTED:
        return settings.poll_max_concurrent_open_self_hosted
    return settings.poll_max_concurrent_open_free


def effective_limits_for(user: User) -> dict:
    """Public-shape config the create form needs. Mirrors the
    notifications/server-config style — operator-set policy that the
    backend will enforce anyway, surfaced so the UI can clamp inputs
    and signal upsell paths to free-tier users."""
    min_s, max_s = close_bounds_for_tier(user.tier)
    return {
        "tier": user.tier,
        "min_close_seconds": min_s,
        "max_close_seconds": max_s,
        "default_retention_days": settings.poll_retention_default_days,
        "max_retention_days": max_retention_days_for_tier(user.tier),
        "max_concurrent_open_polls": max_concurrent_open_for_tier(user.tier),
    }


def validate_retention_days(retention_days: int, *, user: User) -> str | None:
    """Return an error string when retention exceeds the user's tier
    cap. None when within bounds. Treats 0 cap as unlimited."""
    cap = max_retention_days_for_tier(user.tier)
    if cap > 0 and retention_days > cap:
        return (
            f"retention_days must be no more than {cap} days for your "
            f"account tier."
        )
    return None


def validate_close_window(
    closes_at: datetime, *, user: User, now: datetime | None = None
) -> str | None:
    """Return an error string if closes_at is out of bounds, else None."""
    now = now or datetime.now(UTC)
    delta = (closes_at - now).total_seconds()
    min_s, max_s = close_bounds_for_tier(user.tier)
    if delta < min_s:
        return (
            f"closes_at must be at least {min_s // 60} minutes in the future."
        )
    if max_s > 0 and delta > max_s:
        return (
            f"closes_at must be no more than {max_s // 86400} days in the future "
            f"for your account tier."
        )
    return None


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------


def encrypt_text(plaintext: str, aad: bytes) -> str:
    return encrypt(plaintext, aad=aad)


def decrypt_text(ciphertext: str | None, aad: bytes) -> str | None:
    if ciphertext is None:
        return None
    try:
        return decrypt(ciphertext, aad=aad)
    except Exception:
        logger.warning("poll content failed to decrypt; returning empty string")
        return ""


# ---------------------------------------------------------------------------
# Front-state guard
# ---------------------------------------------------------------------------


async def member_is_currently_fronting(
    db: AsyncSession, *, system_id: uuid.UUID, member_id: uuid.UUID
) -> bool:
    """True if the supplied member is part of any open Front in this system.

    "Open" = ended_at IS NULL. Multiple co-fronters on the same Front row
    is the normal case in Sheaf, so we just need at least one open Front
    that contains this member id.
    """
    result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(
            Front.system_id == system_id,
            Front.ended_at.is_(None),
        )
    )
    return any(
        any(m.id == member_id for m in front.members)
        for front in result.scalars().all()
    )


async def current_front_member_ids(
    db: AsyncSession, *, system_id: uuid.UUID
) -> list[uuid.UUID]:
    """Member ids in any currently-open front for this system."""
    result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(
            Front.system_id == system_id,
            Front.ended_at.is_(None),
        )
    )
    seen: list[uuid.UUID] = []
    seen_set: set[uuid.UUID] = set()
    for front in result.scalars().all():
        for m in front.members:
            if m.id not in seen_set:
                seen.append(m.id)
                seen_set.add(m.id)
    return seen


# ---------------------------------------------------------------------------
# Vote casting
# ---------------------------------------------------------------------------


class VoteError(Exception):
    """Raised by record_vote/withdraw_vote when validation fails. The
    API layer maps the message into a 400."""


async def record_vote(
    db: AsyncSession,
    *,
    poll: Poll,
    voted_as_member_id: uuid.UUID,
    option_ids: list[uuid.UUID],
    actor: User,
) -> PollVote:
    """Cast or change a vote on a poll.

    - Refuses if the poll is closed.
    - Refuses if any option_id isn't part of the poll.
    - Refuses if the kind is single_choice and more than one option supplied.
    - Refuses if the voted-as member isn't currently fronting AND the
      poll was created with `restrict_voting_to_fronters=True`. Polls
      without that flag accept votes from any system member regardless
      of front state, matching the journals authoring model.

    Writes both the current-state poll_votes row and the audit event in
    the same transaction. The caller commits.
    """
    now = datetime.now(UTC)
    if poll.closes_at <= now:
        raise VoteError("Poll is closed.")

    if not option_ids:
        raise VoteError("At least one option must be selected.")
    if poll.kind == "single_choice" and len(option_ids) != 1:
        raise VoteError("Single-choice polls require exactly one option.")

    valid_option_ids = {opt.id for opt in poll.options}
    if not set(option_ids).issubset(valid_option_ids):
        raise VoteError("One or more option_ids do not belong to this poll.")
    # De-duplicate while preserving order — same option twice is equivalent
    # to one vote, no need to error.
    seen: set[uuid.UUID] = set()
    deduped_options: list[uuid.UUID] = []
    for opt_id in option_ids:
        if opt_id not in seen:
            seen.add(opt_id)
            deduped_options.append(opt_id)

    member = await db.get(Member, voted_as_member_id)
    if member is None or member.system_id != poll.system_id:
        raise VoteError("voted_as_member_id is not a member of this system.")

    if member.is_custom_front and not poll.include_custom_fronts:
        raise VoteError(
            "This poll does not accept votes from custom fronts."
        )

    if poll.restrict_voting_to_fronters and not await member_is_currently_fronting(
        db, system_id=poll.system_id, member_id=voted_as_member_id
    ):
        raise VoteError(
            "This poll only accepts votes from members in the current front."
        )

    fronting_ids = await current_front_member_ids(db, system_id=poll.system_id)

    # Look up the current vote (if any) — determines cast vs change.
    existing = await db.execute(
        select(PollVote).where(
            PollVote.poll_id == poll.id,
            PollVote.voted_as_member_id == voted_as_member_id,
        )
    )
    current = existing.scalar_one_or_none()

    if current is None:
        action = PollVoteAction.CAST
        vote = PollVote(
            id=uuid.uuid4(),
            poll_id=poll.id,
            voted_as_member_id=voted_as_member_id,
            option_ids=deduped_options,
        )
        db.add(vote)
    else:
        action = PollVoteAction.CHANGE
        current.option_ids = deduped_options
        vote = current

    db.add(
        PollVoteEvent(
            id=uuid.uuid4(),
            poll_id=poll.id,
            voted_as_member_id=voted_as_member_id,
            action=action.value,
            option_ids=deduped_options,
            fronting_member_ids=fronting_ids,
            actor_user_id=actor.id,
        )
    )
    return vote


async def withdraw_vote(
    db: AsyncSession,
    *,
    poll: Poll,
    voted_as_member_id: uuid.UUID,
    actor: User,
) -> bool:
    """Withdraw a member's vote. Returns True if a row was removed.

    Audit event is written regardless — the absence of a withdraw event
    when one was attempted is more confusing than an apparent no-op.
    """
    now = datetime.now(UTC)
    if poll.closes_at <= now:
        raise VoteError("Poll is closed.")

    if poll.restrict_voting_to_fronters and not await member_is_currently_fronting(
        db, system_id=poll.system_id, member_id=voted_as_member_id
    ):
        raise VoteError(
            "The voted-as member must be part of the current front to withdraw."
        )

    fronting_ids = await current_front_member_ids(db, system_id=poll.system_id)

    existing = await db.execute(
        select(PollVote).where(
            PollVote.poll_id == poll.id,
            PollVote.voted_as_member_id == voted_as_member_id,
        )
    )
    current = existing.scalar_one_or_none()
    removed_options: list[uuid.UUID] = current.option_ids if current else []

    if current is not None:
        await db.delete(current)

    db.add(
        PollVoteEvent(
            id=uuid.uuid4(),
            poll_id=poll.id,
            voted_as_member_id=voted_as_member_id,
            action=PollVoteAction.WITHDRAW.value,
            option_ids=removed_options,
            fronting_member_ids=fronting_ids,
            actor_user_id=actor.id,
        )
    )
    return current is not None


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def is_results_visible(poll: Poll, *, now: datetime | None = None) -> bool:
    """Whether tally + audit log are visible right now.

    Live polls always show results. End-only polls hide them until close.
    """
    if poll.results_visibility == "live":
        return True
    now = now or datetime.now(UTC)
    return poll.closes_at <= now


def tally_for(poll: Poll) -> list[tuple[uuid.UUID, int]]:
    """Per-option vote counts. Multi-choice votes count once per option.

    Returns a list of (option_id, count) ordered by option position so
    the UI can render in a stable order regardless of dict iteration.
    """
    counter: Counter[uuid.UUID] = Counter()
    for v in poll.votes:
        for opt_id in v.option_ids:
            counter[opt_id] += 1
    return [(opt.id, counter.get(opt.id, 0)) for opt in poll.options]


def purges_at(poll: Poll) -> datetime:
    """Wallclock time at which the cleanup job will delete this poll."""
    return poll.closes_at + timedelta(days=poll.retention_days)


# ---------------------------------------------------------------------------
# Cleanup tick (registered as a job)
# ---------------------------------------------------------------------------


async def purge_expired_polls(db: AsyncSession) -> int:
    """Delete polls whose closes_at + retention_days has elapsed.

    Cascades remove options, votes, and audit events (DB-level ON DELETE
    CASCADE on poll_options / poll_votes / poll_vote_events).

    Nothing-silent: before deleting, group the expiring polls by owning user
    and leave one content-free RETENTION_PRUNED account-activity trace per
    affected user (detail {"polls_purged": n}), mirroring the front-retention
    and revision-GC sweeps. A sweep that deletes with no trail is invisible
    until someone reads the job internals, so every automated deletion leaves
    one.
    """
    now = datetime.now(UTC)

    # Expiry is expressed entirely in Poll columns, so filter in SQL instead of
    # scanning the whole table into Python: closes_at + retention_days days
    # <= now. make_interval(days => retention_days) keeps the per-poll integer
    # as an interval without hardcoding a day count.
    expired_predicate = (
        Poll.closes_at + func.make_interval(0, 0, 0, Poll.retention_days) <= now
    )

    # Per-user counts, taken before the delete so the trace matches what was
    # actually removed.
    per_user = await db.execute(
        select(System.user_id, func.count(Poll.id))
        .join(System, Poll.system_id == System.id)
        .where(expired_predicate)
        .group_by(System.user_id)
    )
    user_counts = {uid: cnt for uid, cnt in per_user.all()}

    result = await db.execute(delete(Poll).where(expired_predicate))
    purged = result.rowcount or 0

    for uid, cnt in user_counts.items():
        if cnt:
            await log_activity(
                db,
                user_id=uid,
                action=ActivityAction.RETENTION_PRUNED,
                actor_type=ActivityActorType.SYSTEM,
                detail={"polls_purged": cnt},
            )

    return purged

"""Polls API.

CRUD + voting endpoints. The vote endpoint enforces the front-state
guard via `record_vote`. Delete is gated by destructive-auth and can
be safeguarded via System Safety like other destructive actions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.config import settings
from sheaf.database import get_db
from sheaf.models.pending_action import PendingActionType
from sheaf.models.poll import Poll, PollOption, PollVote, PollVoteEvent
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.observability.metrics import (
    polls_created_total,
    tier_label,
    tier_limit_hits_total,
)
from sheaf.schemas.member import MemberDeleteConfirm
from sheaf.schemas.poll import (
    PollAuditRead,
    PollCreate,
    PollOptionRead,
    PollRead,
    PollTallyEntry,
    PollVoteRead,
    VoteCast,
    VoteEventRead,
)
from sheaf.services.polls import (
    VoteError,
    decrypt_text,
    effective_limits_for,
    encrypt_text,
    is_results_visible,
    max_concurrent_open_for_tier,
    purges_at,
    record_vote,
    tally_for,
    validate_close_window,
    validate_retention_days,
    withdraw_vote,
)
from sheaf.services.system_safety import (
    is_safeguarded,
    pending_finalize_after_by_target,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/polls", tags=["polls"])


# --- Server-config (per-user effective limits) ----------------------------


@router.get("/server-config")
async def get_server_config(
    user: User = Depends(get_current_user),
):
    """Effective per-tier limits for the calling user.

    Surfaces the values the backend will enforce on `POST /polls` so
    the frontend can clamp the create form inputs (max close window,
    max retention days, max concurrent open polls) and decide when to
    show upsell hints. Mirrors the shape of `/notifications/server-config`.
    """
    return effective_limits_for(user)


# --- Helpers ---------------------------------------------------------------


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )
    return system


async def _get_owned_poll(
    poll_id: uuid.UUID, system: System, db: AsyncSession
) -> Poll:
    result = await db.execute(
        select(Poll)
        .options(
            selectinload(Poll.options),
            selectinload(Poll.votes),
        )
        .where(Poll.id == poll_id, Poll.system_id == system.id)
    )
    poll = result.scalar_one_or_none()
    if poll is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Poll not found"
        )
    return poll


def _to_read(
    poll: Poll,
    *,
    now: datetime | None = None,
    pending_delete_at: datetime | None = None,
    total_votes_override: int | None = None,
) -> PollRead:
    """Project a Poll to its API shape.

    `total_votes_override` lets the list endpoint pass a pre-computed
    count rather than touching `poll.votes` (which would force loading
    every vote row even when results are hidden and the votes / tally
    fields would be `None` anyway). When the override is provided we
    treat that as a signal that `poll.votes` is NOT eager-loaded, so
    the response omits per-vote detail and the per-option tally —
    those live on the detail endpoint, which loads votes explicitly.
    When omitted (detail path), falls back to the eager-loaded
    `poll.votes` and computes tally + per-vote rows normally.
    """
    now = now or datetime.now(UTC)
    visible = is_results_visible(poll, now=now)
    list_mode = total_votes_override is not None
    options = [
        PollOptionRead(
            id=opt.id,
            text=decrypt_text(opt.text) or "",
            position=opt.position,
        )
        for opt in sorted(poll.options, key=lambda o: o.position)
    ]
    tally = (
        None if list_mode or not visible
        else [
            PollTallyEntry(option_id=opt_id, count=count)
            for opt_id, count in tally_for(poll)
        ]
    )
    votes = (
        None if list_mode or not visible
        else [
            PollVoteRead(
                voted_as_member_id=v.voted_as_member_id,
                option_ids=list(v.option_ids),
                created_at=v.created_at,
                updated_at=v.updated_at,
            )
            for v in poll.votes
        ]
    )
    total = total_votes_override if list_mode else len(poll.votes)
    return PollRead(
        id=poll.id,
        system_id=poll.system_id,
        question=decrypt_text(poll.question) or "",
        description=decrypt_text(poll.description),
        kind=poll.kind,
        results_visibility=poll.results_visibility,
        closes_at=poll.closes_at,
        retention_days=poll.retention_days,
        include_custom_fronts=poll.include_custom_fronts,
        restrict_voting_to_fronters=poll.restrict_voting_to_fronters,
        options=options,
        is_closed=poll.closes_at <= now,
        closed_since=poll.closes_at if poll.closes_at <= now else None,
        purges_at=purges_at(poll),
        total_votes=total,
        tally=tally,
        votes=votes,
        created_at=poll.created_at,
        updated_at=poll.updated_at,
        pending_delete_at=pending_delete_at,
    )


# --- CRUD -----------------------------------------------------------------


@router.get("", response_model=list[PollRead])
async def list_polls(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    # Don't selectinload(Poll.votes) here: results are hidden for any
    # poll whose results_visibility gates haven't fired, and even
    # when visible the list view doesn't need per-vote rows (only the
    # tally + total). A single COUNT-per-poll aggregation replaces
    # what was previously every vote row loaded into Python.
    result = await db.execute(
        select(Poll)
        .options(selectinload(Poll.options))
        .where(Poll.system_id == system.id)
        .order_by(Poll.created_at.desc())
    )
    now = datetime.now(UTC)
    polls = list(result.scalars().all())

    total_votes_by_poll: dict[uuid.UUID, int] = {}
    if polls:
        counts = await db.execute(
            select(PollVote.poll_id, func.count(PollVote.id))
            .where(PollVote.poll_id.in_([p.id for p in polls]))
            .group_by(PollVote.poll_id)
        )
        total_votes_by_poll = {row[0]: row[1] for row in counts.all()}

    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.POLL_DELETE
    )
    return [
        _to_read(
            p,
            now=now,
            pending_delete_at=pending.get(p.id),
            total_votes_override=total_votes_by_poll.get(p.id, 0),
        )
        for p in polls
    ]


@router.post(
    "",
    response_model=PollRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("polls:write"))],
)
async def create_poll(
    body: PollCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)

    # closes_at must be a tz-aware datetime in the future and within tier bounds.
    if body.closes_at.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="closes_at must include a timezone offset.",
        )
    err = validate_close_window(body.closes_at, user=user)
    if err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)

    retention_days = body.retention_days or settings.poll_retention_default_days
    err = validate_retention_days(retention_days, user=user)
    if err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)

    # Concurrent open polls cap. Counts polls in this system whose
    # deadline is still in the future. Closed polls don't count toward
    # the cap — they're read-only artefacts at that point.
    concurrent_cap = max_concurrent_open_for_tier(user.tier)
    if concurrent_cap > 0:
        from sqlalchemy import func

        # Lock the system row so the open-poll cap can't be raced past by
        # simultaneous creates each passing the count check.
        await db.execute(
            select(System.id).where(System.id == system.id).with_for_update()
        )

        now = datetime.now(UTC)
        open_count_result = await db.execute(
            select(func.count(Poll.id)).where(
                Poll.system_id == system.id,
                Poll.closes_at > now,
            )
        )
        open_count = open_count_result.scalar_one()
        if open_count >= concurrent_cap:
            tier_limit_hits_total.labels(
                limit="polls_concurrent", tier=tier_label(user.tier),
            ).inc()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Maximum {concurrent_cap} open polls allowed for your "
                    f"account tier. Wait for an existing poll to close, "
                    f"delete one, or upgrade for a larger cap."
                ),
            )

    poll = Poll(
        id=uuid.uuid4(),
        system_id=system.id,
        question=encrypt_text(body.question),
        description=encrypt_text(body.description) if body.description else None,
        kind=body.kind,
        results_visibility=body.results_visibility,
        closes_at=body.closes_at,
        retention_days=retention_days,
        include_custom_fronts=body.include_custom_fronts,
        restrict_voting_to_fronters=body.restrict_voting_to_fronters,
    )
    for index, opt in enumerate(body.options):
        poll.options.append(
            PollOption(
                id=uuid.uuid4(),
                text=encrypt_text(opt.text),
                position=index,
            )
        )
    db.add(poll)
    await db.commit()
    polls_created_total.inc()
    refreshed = await _get_owned_poll(poll.id, system, db)
    return _to_read(refreshed)


@router.get("/{poll_id}", response_model=PollRead)
async def get_poll(
    poll_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    poll = await _get_owned_poll(poll_id, system, db)
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.POLL_DELETE
    )
    return _to_read(poll, pending_delete_at=pending.get(poll.id))


@router.delete(
    "/{poll_id}",
    dependencies=[Depends(require_scope("polls:delete"))],
)
async def delete_poll(
    poll_id: uuid.UUID,
    body: MemberDeleteConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a poll.

    Mirrors the destructive-action pattern used elsewhere: step-up auth
    via `verify_destructive_auth`, optional pending-action queue when
    System Safety has the polls category turned on with a non-zero grace
    period.
    """
    system = await _get_user_system(user, db)
    poll = await _get_owned_poll(poll_id, system, db)
    await verify_destructive_auth(
        user,
        system,
        body.password if body else None,
        body.totp_code if body else None,
        db,
    )

    if is_safeguarded(system, PendingActionType.POLL_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.POLL_DELETE,
            target_id=poll.id,
            target_label=decrypt_text(poll.question) or "(unnamed poll)",
        )
        await db.commit()
        await db.refresh(pending)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "pending_action_id": str(pending.id),
                "finalize_after": pending.finalize_after.isoformat(),
            },
        )

    await db.delete(poll)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Voting ---------------------------------------------------------------


@router.post(
    "/{poll_id}/votes",
    response_model=PollVoteRead,
    dependencies=[Depends(require_scope("polls:write"))],
)
async def cast_vote(
    poll_id: uuid.UUID,
    body: VoteCast,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    poll = await _get_owned_poll(poll_id, system, db)
    try:
        vote = await record_vote(
            db,
            poll=poll,
            voted_as_member_id=body.voted_as_member_id,
            option_ids=body.option_ids,
            actor=user,
        )
    except VoteError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    await db.commit()
    await db.refresh(vote)
    return PollVoteRead(
        voted_as_member_id=vote.voted_as_member_id,
        option_ids=list(vote.option_ids),
        created_at=vote.created_at,
        updated_at=vote.updated_at,
    )


@router.delete(
    "/{poll_id}/votes/{voted_as_member_id}",
    dependencies=[Depends(require_scope("polls:write"))],
)
async def withdraw_vote_endpoint(
    poll_id: uuid.UUID,
    voted_as_member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    system = await _get_user_system(user, db)
    poll = await _get_owned_poll(poll_id, system, db)
    try:
        await withdraw_vote(
            db,
            poll=poll,
            voted_as_member_id=voted_as_member_id,
            actor=user,
        )
    except VoteError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Audit log ------------------------------------------------------------


@router.get("/{poll_id}/audit", response_model=PollAuditRead)
async def get_audit_log(
    poll_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Audit log of every cast/change/withdraw on this poll.

    Visibility tracks the tally: hidden until close on `end_only` polls
    (otherwise the count of `cast` events leaks the running total) and
    available for the lifetime of the poll on `live` polls. After close
    the log is always visible until retention expires.
    """
    system = await _get_user_system(user, db)
    poll = await _get_owned_poll(poll_id, system, db)
    visible = is_results_visible(poll)
    if not visible:
        return PollAuditRead(poll_id=poll.id, is_visible=False, events=[])

    result = await db.execute(
        select(PollVoteEvent)
        .where(PollVoteEvent.poll_id == poll.id)
        .order_by(PollVoteEvent.created_at)
    )
    events = [
        VoteEventRead(
            id=e.id,
            voted_as_member_id=e.voted_as_member_id,
            action=e.action,
            option_ids=list(e.option_ids),
            fronting_member_ids=list(e.fronting_member_ids),
            actor_user_id=e.actor_user_id,
            created_at=e.created_at,
        )
        for e in result.scalars().all()
    ]
    return PollAuditRead(poll_id=poll.id, is_visible=True, events=events)

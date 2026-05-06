import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.crypto import decrypt, encrypt
from sheaf.database import get_db
from sheaf.models.front import Front
from sheaf.models.member import Member, front_members
from sheaf.models.pending_action import PendingActionType
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.front import FrontCreate, FrontRead, FrontUpdate
from sheaf.schemas.member import MemberDeleteConfirm
from sheaf.services.notifications.events import (
    emit_front_change,
    snapshot_front_state,
)
from sheaf.services.system_safety import (
    is_safeguarded,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/fronts", tags=["fronts"])


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")
    return system


def _front_to_read(
    front: Front,
    *,
    member_since: dict[str, datetime] | None = None,
    member_since_capped: list[str] | None = None,
) -> FrontRead:
    """Project a Front row to its API shape.

    `member_since` is the per-member effective "fronting since" map.
    When omitted, defaults to {member_id: front.started_at} for each
    member — the literal-entry view used by history endpoints and any
    caller that doesn't want to pay for the walk-back.

    `member_since_capped` lists member ids whose chain hit the
    walk-back depth limit; the returned timestamp is a lower bound,
    not the true chain start. Frontends should render those with a
    "> X ago" prefix to be honest about precision.
    """
    if member_since is None:
        member_since = {str(m.id): front.started_at for m in front.members}
    return FrontRead(
        id=front.id,
        system_id=front.system_id,
        started_at=front.started_at,
        ended_at=front.ended_at,
        member_ids=[m.id for m in front.members],
        custom_status=decrypt(front.custom_status) if front.custom_status else None,
        member_since=member_since,
        member_since_capped=member_since_capped or [],
    )


# Walk-back depth cap: pathological cycles aside, real chains are
# typically 1-3 entries. 500 is a generous bound that prevents a
# corrupted-data edge case from running unbounded queries while still
# covering anyone who switches every few minutes for many hours.
# When the cap *is* hit, the response flags the affected member so the
# UI can render "> X ago" instead of silently under-reporting. Easy to
# raise later if the flag actually starts surfacing in real usage.
_COALESCE_MAX_DEPTH = 500


async def _coalesced_started_at(
    db: AsyncSession,
    *,
    system_id: uuid.UUID,
    member_id: uuid.UUID,
    starting_at: datetime,
) -> tuple[datetime, bool]:
    """Walk back through contiguous front entries for a member.

    A "contiguous chain" links Front rows where member M appears in
    both, AND the previous front's ended_at exactly matches the next
    front's started_at. Any gap (or member absence) breaks the chain.
    Returns (earliest_started_at, capped) — `capped` is True if the
    walk-back hit the depth limit (chain was longer than the cap and
    the returned timestamp is a lower bound, not the true chain start).
    """
    cursor = starting_at
    seen: set[uuid.UUID] = set()
    for _ in range(_COALESCE_MAX_DEPTH):
        result = await db.execute(
            select(Front)
            .join(front_members, front_members.c.front_id == Front.id)
            .where(
                Front.system_id == system_id,
                Front.ended_at == cursor,
                front_members.c.member_id == member_id,
            )
            .limit(1)
        )
        prev = result.scalar_one_or_none()
        if prev is None or prev.id in seen:
            return cursor, False
        seen.add(prev.id)
        cursor = prev.started_at
    # Loop finished without finding the chain start — there's at least
    # one more entry beyond what we walked. Caller should display the
    # timestamp as a lower bound (e.g. "> 8h ago").
    return cursor, True


async def _build_coalesced_member_since(
    db: AsyncSession, system: System, fronts: list[Front]
) -> dict[uuid.UUID, tuple[dict[str, datetime], list[str]]]:
    """For each given front, build (since_map, capped_member_ids).

    When `system.coalesce_contiguous_fronts` is False, returns the
    literal-entry view for each member with no capped members.
    Otherwise walks back per member to find the earliest chain start.
    """
    out: dict[uuid.UUID, tuple[dict[str, datetime], list[str]]] = {}
    for front in fronts:
        per_member: dict[str, datetime] = {}
        capped: list[str] = []
        for member in front.members:
            if system.coalesce_contiguous_fronts:
                ts, hit_cap = await _coalesced_started_at(
                    db,
                    system_id=system.id,
                    member_id=member.id,
                    starting_at=front.started_at,
                )
                per_member[str(member.id)] = ts
                if hit_cap:
                    capped.append(str(member.id))
            else:
                per_member[str(member.id)] = front.started_at
        out[front.id] = (per_member, capped)
    return out


@router.get("", response_model=list[FrontRead])
async def list_fronts(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(Front.system_id == system.id)
        .order_by(Front.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_front_to_read(f) for f in result.scalars().all()]


@router.get("/current", response_model=list[FrontRead])
async def get_current_fronts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(Front.system_id == system.id, Front.ended_at.is_(None))
        .order_by(Front.started_at.desc())
    )
    fronts = list(result.scalars().all())
    member_since_map = await _build_coalesced_member_since(db, system, fronts)
    return [
        _front_to_read(
            f,
            member_since=member_since_map[f.id][0],
            member_since_capped=member_since_map[f.id][1],
        )
        for f in fronts
    ]


@router.post(
    "",
    response_model=FrontRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("fronts:write"))],
)
async def create_front(
    body: FrontCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)

    # Validate member IDs belong to this system
    result = await db.execute(
        select(Member).where(
            Member.id.in_(body.member_ids),
            Member.system_id == system.id,
        )
    )
    members = list(result.scalars().all())
    if len(members) != len(body.member_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more member IDs are invalid",
        )

    before_state = await snapshot_front_state(db, system.id)

    # Compute the new front's started_at up-front so we can use it as the
    # boundary timestamp for any auto-ended fronts. Strict equality between
    # f_old.ended_at and f_new.started_at is what `coalesce_contiguous_fronts`
    # relies on to walk a member back through chained entries — if these
    # were two separate datetime.now() calls a few ms apart the chain
    # would always break.
    new_started_at = body.started_at or datetime.now(UTC)

    # Resolve replace_fronts: explicit value beats system default
    should_replace = (
        body.replace_fronts if body.replace_fronts is not None else system.replace_fronts_default
    )
    if should_replace:
        open_fronts = await db.execute(
            select(Front)
            .where(Front.system_id == system.id, Front.ended_at.is_(None))
        )
        for f in open_fronts.scalars().all():
            f.ended_at = new_started_at
    else:
        # Block exact-set duplicates: if an open front already has this exact
        # member set, two fronts with the same composition has no useful
        # semantics (notifications, current-front queries, etc. would treat
        # them as redundant). Different compositions are still allowed; the
        # owner can keep {Alice} fronting and add {Alice, Bob} alongside.
        new_set = set(body.member_ids)
        existing = await db.execute(
            select(Front)
            .options(selectinload(Front.members))
            .where(Front.system_id == system.id, Front.ended_at.is_(None))
        )
        for f in existing.scalars().all():
            if {m.id for m in f.members} == new_set:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "A front with these exact members is already active. "
                        "Either end the existing front first, or pick a "
                        "different combination."
                    ),
                )

    front = Front(
        system_id=system.id,
        started_at=new_started_at,
        custom_status=encrypt(body.custom_status) if body.custom_status else None,
        members=members,
    )
    db.add(front)
    await db.flush()

    after_state = await snapshot_front_state(db, system.id)
    await emit_front_change(
        db, system_id=system.id, before=before_state, after=after_state
    )

    await db.commit()
    await db.refresh(front, ["members"])
    return _front_to_read(front)


@router.patch(
    "/{front_id}",
    response_model=FrontRead,
    dependencies=[Depends(require_scope("fronts:write"))],
)
async def update_front(
    front_id: uuid.UUID,
    body: FrontUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(Front.id == front_id, Front.system_id == system.id)
    )
    front = result.scalar_one_or_none()
    if front is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Front not found")

    before_state = await snapshot_front_state(db, system.id)

    if body.ended_at is not None:
        front.ended_at = body.ended_at

    # custom_status uses presence-in-body to distinguish "omit" from
    # "explicitly clear". model_fields_set is Pydantic v2's per-instance
    # set of field names that were actually supplied in the input.
    if "custom_status" in body.model_fields_set:
        front.custom_status = (
            encrypt(body.custom_status) if body.custom_status else None
        )

    if body.member_ids is not None:
        member_result = await db.execute(
            select(Member).where(
                Member.id.in_(body.member_ids),
                Member.system_id == system.id,
            )
        )
        members = list(member_result.scalars().all())
        if len(members) != len(body.member_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more member IDs are invalid",
            )
        front.members = members

    await db.flush()

    after_state = await snapshot_front_state(db, system.id)
    await emit_front_change(
        db, system_id=system.id, before=before_state, after=after_state
    )

    await db.commit()
    await db.refresh(front, ["members"])
    return _front_to_read(front)


@router.delete(
    "/{front_id}",
    dependencies=[Depends(require_scope("fronts:delete"))],
)
async def delete_front(
    front_id: uuid.UUID,
    body: MemberDeleteConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    system = await _get_user_system(user, db)
    verify_destructive_auth(
        user,
        system,
        body.password if body else None,
        body.totp_code if body else None,
    )
    result = await db.execute(
        select(Front).where(Front.id == front_id, Front.system_id == system.id)
    )
    front = result.scalar_one_or_none()
    if front is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Front not found")

    if is_safeguarded(system, PendingActionType.FRONT_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.FRONT_DELETE,
            target_id=front.id,
            target_label=f"Front starting {front.started_at.isoformat()}",
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

    await db.delete(front)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

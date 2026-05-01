import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.front import Front
from sheaf.models.member import Member
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


def _front_to_read(front: Front) -> FrontRead:
    return FrontRead(
        id=front.id,
        system_id=front.system_id,
        started_at=front.started_at,
        ended_at=front.ended_at,
        member_ids=[m.id for m in front.members],
    )


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
    return [_front_to_read(f) for f in result.scalars().all()]


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

    # Resolve replace_fronts: explicit value beats system default
    should_replace = (
        body.replace_fronts if body.replace_fronts is not None else system.replace_fronts_default
    )
    if should_replace:
        open_fronts = await db.execute(
            select(Front)
            .where(Front.system_id == system.id, Front.ended_at.is_(None))
        )
        now = datetime.now(UTC)
        for f in open_fronts.scalars().all():
            f.ended_at = now
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
        started_at=body.started_at or datetime.now(UTC),
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

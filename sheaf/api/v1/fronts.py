import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.models.front import Front
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.front import FrontCreate, FrontRead, FrontUpdate

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


@router.post("", response_model=FrontRead, status_code=status.HTTP_201_CREATED)
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

    front = Front(
        system_id=system.id,
        started_at=body.started_at or datetime.now(UTC),
        members=members,
    )
    db.add(front)
    await db.flush()
    return _front_to_read(front)


@router.patch("/{front_id}", response_model=FrontRead)
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

    return _front_to_read(front)


@router.delete("/{front_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_front(
    front_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Front).where(Front.id == front_id, Front.system_id == system.id)
    )
    front = result.scalar_one_or_none()
    if front is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Front not found")
    await db.delete(front)

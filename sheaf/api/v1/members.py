import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.config import settings
from sheaf.database import get_db
from sheaf.models.member import Member
from sheaf.models.pending_action import PendingActionType
from sheaf.models.system import System
from sheaf.models.user import User, UserTier
from sheaf.schemas.member import MemberCreate, MemberDeleteConfirm, MemberRead, MemberUpdate
from sheaf.services.system_safety import (
    is_safeguarded,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/members", tags=["members"])


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")
    return system


async def _get_own_member(
    member_id: uuid.UUID, system: System, db: AsyncSession
) -> Member:
    result = await db.execute(
        select(Member).where(Member.id == member_id, Member.system_id == system.id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    return member


@router.get("", response_model=list[MemberRead])
async def list_members(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Member).where(Member.system_id == system.id).order_by(Member.name)
    )
    return result.scalars().all()


_MEMBER_LIMIT_MAP = {
    UserTier.FREE: lambda: settings.member_limit_free,
    UserTier.PLUS: lambda: settings.member_limit_plus,
    UserTier.SELF_HOSTED: lambda: settings.member_limit_selfhosted,
}


def _get_member_limit(user: User) -> int:
    """Return the member limit for a user. 0 means unlimited."""
    if user.member_limit is not None:
        return user.member_limit
    return _MEMBER_LIMIT_MAP.get(user.tier, lambda: 0)()


@router.post(
    "",
    response_model=MemberRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("members:write"))],
)
async def create_member(
    body: MemberCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)

    limit = _get_member_limit(user)
    if limit > 0:
        result = await db.execute(
            select(func.count()).where(Member.system_id == system.id)
        )
        count = result.scalar_one()
        if count >= limit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Member limit reached ({limit}). Contact support for an increase.",
            )

    member = Member(system_id=system.id, **body.model_dump())
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


@router.get("/{member_id}", response_model=MemberRead)
async def get_member(
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    return await _get_own_member(member_id, system, db)


@router.patch(
    "/{member_id}",
    response_model=MemberRead,
    dependencies=[Depends(require_scope("members:write"))],
)
async def update_member(
    member_id: uuid.UUID,
    body: MemberUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db)
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(member, key, value)
    await db.commit()
    await db.refresh(member)
    return member


@router.delete(
    "/{member_id}",
    dependencies=[Depends(require_scope("members:delete"))],
)
async def delete_member(
    member_id: uuid.UUID,
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
    member = await _get_own_member(member_id, system, db)

    if is_safeguarded(system, PendingActionType.MEMBER_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.MEMBER_DELETE,
            target_id=member.id,
            target_label=member.display_name or member.name,
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

    await db.delete(member)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

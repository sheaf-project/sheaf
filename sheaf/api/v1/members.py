import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user
from sheaf.auth.passwords import verify_password
from sheaf.auth.totp import verify_code
from sheaf.crypto import decrypt
from sheaf.database import get_db
from sheaf.models.member import Member
from sheaf.models.system import DeleteConfirmation, System
from sheaf.models.user import User
from sheaf.schemas.member import MemberCreate, MemberDeleteConfirm, MemberRead, MemberUpdate

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


@router.post("", response_model=MemberRead, status_code=status.HTTP_201_CREATED)
async def create_member(
    body: MemberCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = Member(system_id=system.id, **body.model_dump())
    db.add(member)
    await db.flush()
    return member


@router.get("/{member_id}", response_model=MemberRead)
async def get_member(
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    return await _get_own_member(member_id, system, db)


@router.patch("/{member_id}", response_model=MemberRead)
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
    return member


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(
    member_id: uuid.UUID,
    body: MemberDeleteConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    level = system.delete_confirmation

    if level in (DeleteConfirmation.PASSWORD, DeleteConfirmation.BOTH) and (
        not body or not body.password or not verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password required to delete member",
        )

    if level in (DeleteConfirmation.TOTP, DeleteConfirmation.BOTH):
        if not user.totp_enabled:
            pass  # TOTP not configured, skip
        elif not body or not body.totp_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TOTP code required to delete member",
            )
        else:
            secret = decrypt(user.totp_secret)
            if not verify_code(secret, body.totp_code):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid TOTP code",
                )

    member = await _get_own_member(member_id, system, db)
    await db.delete(member)
    await db.flush()

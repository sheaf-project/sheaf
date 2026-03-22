import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.group import Group
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.group import GroupCreate, GroupMemberUpdate, GroupRead, GroupUpdate
from sheaf.schemas.member import MemberRead

router = APIRouter(prefix="/groups", tags=["groups"])


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")
    return system


async def _get_own_group(group_id: uuid.UUID, system: System, db: AsyncSession) -> Group:
    result = await db.execute(
        select(Group).where(Group.id == group_id, Group.system_id == system.id)
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@router.get("", response_model=list[GroupRead])
async def list_groups(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Group).where(Group.system_id == system.id).order_by(Group.name)
    )
    return result.scalars().all()


@router.post(
    "",
    response_model=GroupRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("groups:write"))],
)
async def create_group(
    body: GroupCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)

    if body.parent_id is not None:
        await _get_own_group(body.parent_id, system, db)

    group = Group(system_id=system.id, **body.model_dump())
    db.add(group)
    await db.flush()
    return group


@router.get("/{group_id}", response_model=GroupRead)
async def get_group(
    group_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    return await _get_own_group(group_id, system, db)


@router.patch(
    "/{group_id}",
    response_model=GroupRead,
    dependencies=[Depends(require_scope("groups:write"))],
)
async def update_group(
    group_id: uuid.UUID,
    body: GroupUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    group = await _get_own_group(group_id, system, db)
    update_data = body.model_dump(exclude_unset=True)

    # Validate parent_id if being changed
    if "parent_id" in update_data:
        new_parent_id = update_data["parent_id"]
        if new_parent_id is not None:
            if new_parent_id == group.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A group cannot be its own parent",
                )
            # Verify parent belongs to same system
            await _get_own_group(new_parent_id, system, db)
            # Check for cycles: walk up from the proposed parent
            current = new_parent_id
            visited = {group.id}
            while current is not None:
                if current in visited:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Circular parent reference",
                    )
                visited.add(current)
                parent_result = await db.execute(
                    select(Group).where(Group.id == current, Group.system_id == system.id)
                )
                parent = parent_result.scalar_one_or_none()
                current = parent.parent_id if parent else None

    for key, value in update_data.items():
        setattr(group, key, value)
    return group


@router.delete(
    "/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("groups:delete"))],
)
async def delete_group(
    group_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    group = await _get_own_group(group_id, system, db)
    await db.delete(group)
    await db.flush()


@router.get("/{group_id}/members", response_model=list[MemberRead])
async def get_group_members(
    group_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Group)
        .options(selectinload(Group.members))
        .where(Group.id == group_id, Group.system_id == system.id)
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group.members


@router.put(
    "/{group_id}/members",
    response_model=list[MemberRead],
    dependencies=[Depends(require_scope("groups:write"))],
)
async def set_group_members(
    group_id: uuid.UUID,
    body: GroupMemberUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Group)
        .options(selectinload(Group.members))
        .where(Group.id == group_id, Group.system_id == system.id)
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

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

    group.members = members
    return group.members

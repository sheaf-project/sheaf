import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.pending_action import PendingActionType
from sheaf.models.system import System
from sheaf.models.tag import Tag
from sheaf.models.user import User
from sheaf.schemas.member import MemberDeleteConfirm
from sheaf.schemas.tag import TagCreate, TagRead, TagUpdate
from sheaf.services.system_safety import (
    is_safeguarded,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/tags", tags=["tags"])


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")
    return system


@router.get("", response_model=list[TagRead])
async def list_tags(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Tag).where(Tag.system_id == system.id).order_by(Tag.name)
    )
    return result.scalars().all()


@router.post(
    "",
    response_model=TagRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("tags:write"))],
)
async def create_tag(
    body: TagCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    tag = Tag(system_id=system.id, **body.model_dump())
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


@router.get("/{tag_id}", response_model=TagRead)
async def get_tag(
    tag_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Tag).where(Tag.id == tag_id, Tag.system_id == system.id)
    )
    tag = result.scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    return tag


@router.patch(
    "/{tag_id}",
    response_model=TagRead,
    dependencies=[Depends(require_scope("tags:write"))],
)
async def update_tag(
    tag_id: uuid.UUID,
    body: TagUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Tag).where(Tag.id == tag_id, Tag.system_id == system.id)
    )
    tag = result.scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tag, key, value)
    await db.commit()
    await db.refresh(tag)
    return tag


@router.delete(
    "/{tag_id}",
    dependencies=[Depends(require_scope("tags:delete"))],
)
async def delete_tag(
    tag_id: uuid.UUID,
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
        select(Tag).where(Tag.id == tag_id, Tag.system_id == system.id)
    )
    tag = result.scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")

    if is_safeguarded(system, PendingActionType.TAG_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.TAG_DELETE,
            target_id=tag.id,
            target_label=tag.name,
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

    await db.delete(tag)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.api.deps import get_user_system as _get_user_system
from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.member import Member
from sheaf.models.pending_action import PendingActionType
from sheaf.models.tag import Tag
from sheaf.models.user import User
from sheaf.observability.metrics import tags_created_total
from sheaf.schemas.member import MemberDeleteConfirm, MemberRead
from sheaf.schemas.tag import TagCreate, TagMemberUpdate, TagRead, TagUpdate
from sheaf.services.members import decrypt_member_for_read
from sheaf.services.memberships import member_ids_by_tag, member_ids_for_tag
from sheaf.services.system_safety import (
    is_safeguarded,
    pending_finalize_after_by_target,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/tags", tags=["tags"])



@router.get("", response_model=list[TagRead])
async def list_tags(
    include_member_ids: bool = Query(
        default=False,
        description=(
            "Include each tag's member ids. Off by default so existing "
            "callers' payloads do not grow. Lets a client build a member to "
            "tags map in one request instead of one per tag."
        ),
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Tag).where(Tag.system_id == system.id).order_by(Tag.name)
    )
    tags = list(result.scalars().all())
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.TAG_DELETE
    )
    # One query for every tag, not one per tag: see the note in
    # `sheaf/services/memberships.py`.
    members = (
        await member_ids_by_tag(db, system.id) if include_member_ids else {}
    )
    out: list[TagRead] = []
    for t in tags:
        tr = TagRead.model_validate(t)
        tr.pending_delete_at = pending.get(t.id)
        if include_member_ids:
            # Empty list, never null: null means "you did not ask".
            tr.member_ids = members.get(t.id, [])
        out.append(tr)
    return out


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
    tags_created_total.inc()
    await db.refresh(tag)
    return tag


@router.get("/{tag_id}", response_model=TagRead)
async def get_tag(
    tag_id: uuid.UUID,
    include_member_ids: bool = Query(
        default=False,
        description="Include this tag's member ids. Off by default.",
    ),
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
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.TAG_DELETE
    )
    tr = TagRead.model_validate(tag)
    tr.pending_delete_at = pending.get(tag.id)
    if include_member_ids:
        tr.member_ids = await member_ids_for_tag(db, tag.id)
    return tr


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


@router.get("/{tag_id}/members", response_model=list[MemberRead])
async def get_tag_members(
    tag_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Tag)
        .options(selectinload(Tag.members))
        .where(Tag.id == tag_id, Tag.system_id == system.id)
    )
    tag = result.scalar_one_or_none()
    if tag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )
    return [decrypt_member_for_read(m, user.id) for m in tag.members]


@router.put(
    "/{tag_id}/members",
    response_model=list[MemberRead],
    dependencies=[Depends(require_scope("tags:write"))],
)
async def set_tag_members(
    tag_id: uuid.UUID,
    body: TagMemberUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace the tag's full member set with the body-supplied list.

    Mirrors `PUT /v1/groups/{group_id}/members` — full-replace semantics
    keep the API symmetric and avoid subtle bugs around partial updates.
    For a single add/remove, the caller should fetch first and re-PUT.
    """
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Tag)
        .options(selectinload(Tag.members))
        .where(Tag.id == tag_id, Tag.system_id == system.id)
    )
    tag = result.scalar_one_or_none()
    if tag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )

    member_result = await db.execute(
        select(Member).where(
            Member.id.in_(body.member_ids),
            Member.system_id == system.id,
        )
    )
    members = list(member_result.scalars().all())
    if len(members) != len(set(body.member_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more member IDs are invalid",
        )

    tag.members = members
    await db.commit()
    return [decrypt_member_for_read(m, user.id) for m in members]


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
    await verify_destructive_auth(
        user,
        system,
        body.password if body else None,
        body.totp_code if body else None,
        db,
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

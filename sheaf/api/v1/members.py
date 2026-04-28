import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.config import settings
from sheaf.crypto import blind_index, encrypt
from sheaf.database import get_db
from sheaf.models.content_revision import ContentRevision, ContentRevisionTarget
from sheaf.models.member import Member
from sheaf.models.pending_action import PendingActionType
from sheaf.models.system import System
from sheaf.models.user import User, UserTier
from sheaf.schemas.journal import ContentRevisionRead, RestoreRevisionRequest
from sheaf.schemas.member import MemberCreate, MemberDeleteConfirm, MemberRead, MemberUpdate
from sheaf.services.journals import (
    capture_revision,
    decrypt_revision_for_read,
    delete_revisions_for,
    restore_member_bio_revision,
)
from sheaf.services.members import decrypt_member_for_read, member_plaintext
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
    # Member.name is encrypted ciphertext, so DB-side ORDER BY on it is
    # meaningless. Decrypt then sort by display_name fallback to name.
    result = await db.execute(
        select(Member).where(Member.system_id == system.id)
    )
    members = result.scalars().all()
    decoded = [decrypt_member_for_read(m) for m in members]
    decoded.sort(key=lambda m: (m.display_name or m.name).casefold())
    return decoded


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

    data = body.model_dump()
    plaintext_name: str = data.pop("name")
    plaintext_description: str | None = data.pop("description", None)
    member = Member(
        system_id=system.id,
        name=encrypt(plaintext_name),
        name_hash=blind_index(plaintext_name),
        description=(
            encrypt(plaintext_description) if plaintext_description is not None else None
        ),
        **data,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return decrypt_member_for_read(member)


@router.get("/{member_id}", response_model=MemberRead)
async def get_member(
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db)
    return decrypt_member_for_read(member)


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
    _, current_description = member_plaintext(member)
    if (
        "description" in update_data
        and update_data["description"] != current_description
    ):
        await capture_revision(
            db=db,
            target_type=ContentRevisionTarget.MEMBER_BIO,
            target_id=member.id,
            user=user,
            system_id=system.id,
            title=None,
            body=current_description or "",
        )
    for key, value in update_data.items():
        if key == "name":
            member.name = encrypt(value)
            member.name_hash = blind_index(value)
        elif key == "description":
            member.description = encrypt(value) if value is not None else None
        else:
            setattr(member, key, value)
    await db.commit()
    await db.refresh(member)
    return decrypt_member_for_read(member)


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
            target_label=member.display_name or member_plaintext(member)[0],
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

    await delete_revisions_for(ContentRevisionTarget.MEMBER_BIO, member.id, db)
    await db.delete(member)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{member_id}/revisions",
    response_model=list[ContentRevisionRead],
)
async def list_bio_revisions(
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db)
    result = await db.execute(
        select(ContentRevision)
        .where(
            ContentRevision.target_type
            == ContentRevisionTarget.MEMBER_BIO.value,
            ContentRevision.target_id == member.id,
        )
        .order_by(ContentRevision.created_at.desc())
    )
    return [
        ContentRevisionRead.model_validate(decrypt_revision_for_read(r))
        for r in result.scalars().all()
    ]


@router.post(
    "/{member_id}/restore-revision",
    response_model=MemberRead,
    dependencies=[Depends(require_scope("members:write"))],
)
async def restore_bio_revision(
    member_id: uuid.UUID,
    body: RestoreRevisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db)
    revision = await db.get(ContentRevision, body.revision_id)
    if (
        revision is None
        or revision.target_type != ContentRevisionTarget.MEMBER_BIO.value
        or revision.target_id != member.id
    ):
        raise HTTPException(status_code=404, detail="Revision not found")
    await restore_member_bio_revision(
        db=db, user=user, member=member, revision=revision
    )
    await db.commit()
    await db.refresh(member)
    return decrypt_member_for_read(member)

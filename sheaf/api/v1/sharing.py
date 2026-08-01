"""Owner-side management of share views and grants.

Every endpoint here is scoped to the caller's own system. The asymmetry that
runs through the whole feature shows up in the re-auth rules: anything that
EXPOSES more (creating a grant, adding a member/field/group to a view that is
already shared) is deferred and re-auth-gated when the system has armed the
`profile_visibility` safety category, while anything that exposes LESS
(revoking, rotating, removing, deleting) is immediate and never gated.

The anonymous read surface lives in a separate router; nothing here serves
public content.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.custom_field import CustomFieldDefinition
from sheaf.models.group import Group
from sheaf.models.member import Member, group_members
from sheaf.models.share import (
    ShareGrant,
    ShareGrantStatus,
    ShareView,
    ShareViewField,
    ShareViewGroup,
    ShareViewMember,
)
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.share import (
    ShareAudit,
    ShareAuditEntry,
    ShareGrantCreate,
    ShareGrantCreated,
    ShareGrantRead,
    ShareViewCreate,
    ShareViewFieldAdd,
    ShareViewGroupAdd,
    ShareViewGroupAddResult,
    ShareViewMemberAdd,
    ShareViewRead,
    ShareViewUpdate,
)
from sheaf.services.sharing import (
    add_field_to_view,
    add_member_to_view,
    create_grant,
    expand_group_into_view,
    is_exposure_safeguarded,
    revoke_grant,
    rotate_grant_token,
    view_is_shared,
)
from sheaf.services.system_safety import verify_destructive_auth

router = APIRouter(tags=["sharing"])


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )
    return system


async def _get_view(
    view_id: uuid.UUID, system: System, db: AsyncSession, *, load: bool = False
) -> ShareView:
    """Fetch a view, 404ing if it is not this system's.

    Same 404 for "no such view" and "not yours" so an id from another tenant
    cannot be probed for existence.
    """
    stmt = select(ShareView).where(
        ShareView.id == view_id, ShareView.system_id == system.id
    )
    if load:
        stmt = stmt.options(
            selectinload(ShareView.members),
            selectinload(ShareView.fields),
            selectinload(ShareView.groups),
        )
    result = await db.execute(stmt)
    view = result.scalar_one_or_none()
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Share view not found"
        )
    return view


async def _reauth_if_deferred(
    *,
    db: AsyncSession,
    user: User,
    system: System,
    deferred: bool,
    password: str | None,
    totp_code: str | None,
) -> None:
    """Step-up auth, but only when the action actually waits out a grace window.

    Mirrors the settings path: re-auth is the price of a change that will take
    effect later without further confirmation, not a toll on every edit.
    """
    if deferred:
        await verify_destructive_auth(user, system, password, totp_code, db)


def _view_to_read(view: ShareView, *, is_shared: bool) -> ShareViewRead:
    return ShareViewRead(
        id=view.id,
        name=view.name,
        include_bio=view.include_bio,
        include_fronting=view.include_fronting,
        fronting_show_count=view.fronting_show_count,
        created_at=view.created_at,
        is_shared=is_shared,
        members=[
            {
                "id": m.id,
                "member_id": m.member_id,
                "status": m.status,
                "activates_at": m.activates_at,
            }
            for m in view.members
        ],
        fields=[
            {
                "id": f.id,
                "field_id": f.field_id,
                "status": f.status,
                "activates_at": f.activates_at,
            }
            for f in view.fields
        ],
        groups=[
            {"id": g.id, "group_id": g.group_id, "synced_at": g.synced_at}
            for g in view.groups
        ],
    )


def _grant_to_read(grant: ShareGrant) -> ShareGrantRead:
    """Explicitly enumerated: token_hash must never reach a response."""
    return ShareGrantRead(
        id=grant.id,
        view_id=grant.view_id,
        subject_type=grant.subject_type,
        note=grant.note,
        status=grant.status,
        activates_at=grant.activates_at,
        expires_at=grant.expires_at,
        revoked_at=grant.revoked_at,
        created_at=grant.created_at,
    )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@router.get("/share-views", response_model=list[ShareViewRead])
async def list_share_views(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ShareViewRead]:
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(ShareView)
        .where(ShareView.system_id == system.id)
        .options(
            selectinload(ShareView.members),
            selectinload(ShareView.fields),
            selectinload(ShareView.groups),
        )
        .order_by(ShareView.name)
    )
    views = list(result.scalars().all())

    shared_rows = await db.execute(
        select(ShareGrant.view_id).where(
            ShareGrant.system_id == system.id,
            ShareGrant.revoked_at.is_(None),
            ShareGrant.status.in_(
                [ShareGrantStatus.ACTIVE.value, ShareGrantStatus.PENDING.value]
            ),
        )
    )
    shared = set(shared_rows.scalars().all())
    return [_view_to_read(v, is_shared=v.id in shared) for v in views]


@router.post(
    "/share-views",
    response_model=ShareViewRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("sharing:write"))],
)
async def create_share_view(
    body: ShareViewCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareViewRead:
    """Create an empty view.

    Creating a view exposes nothing on its own (no grant points at it yet), so
    this is deliberately ungated. The gate is on publishing.
    """
    system = await _get_user_system(user, db)
    view = ShareView(
        id=uuid.uuid4(),
        system_id=system.id,
        name=body.name.strip(),
        include_bio=body.include_bio,
        include_fronting=body.include_fronting,
        fronting_show_count=body.fronting_show_count,
    )
    db.add(view)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A share view with that name already exists.",
        ) from None
    await db.refresh(view, ["members", "fields", "groups"])
    return _view_to_read(view, is_shared=False)


@router.get("/share-views/{view_id}", response_model=ShareViewRead)
async def get_share_view(
    view_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareViewRead:
    system = await _get_user_system(user, db)
    view = await _get_view(view_id, system, db, load=True)
    return _view_to_read(view, is_shared=await view_is_shared(db, view.id))


@router.patch(
    "/share-views/{view_id}",
    response_model=ShareViewRead,
    dependencies=[Depends(require_scope("sharing:write"))],
)
async def update_share_view(
    view_id: uuid.UUID,
    body: ShareViewUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareViewRead:
    """Edit a view's settings.

    Turning `include_bio` or `include_fronting` ON while the view is already
    shared exposes more, so it is re-auth-gated the same way adding a member
    is. Turning them off is immediate.
    """
    system = await _get_user_system(user, db)
    view = await _get_view(view_id, system, db, load=True)
    shared = await view_is_shared(db, view.id)

    loosening = shared and (
        (body.include_bio is True and not view.include_bio)
        or (body.include_fronting is True and not view.include_fronting)
        # Switching the fallback from "hide" to "count" reveals that someone
        # not in the view is fronting, which is strictly more than before.
        or (body.fronting_show_count is True and not view.fronting_show_count)
    )
    await _reauth_if_deferred(
        db=db,
        user=user,
        system=system,
        deferred=loosening and is_exposure_safeguarded(system),
        password=body.password,
        totp_code=body.totp_code,
    )

    if body.name is not None:
        view.name = body.name.strip()
    if body.include_bio is not None:
        view.include_bio = body.include_bio
    if body.include_fronting is not None:
        view.include_fronting = body.include_fronting
    if body.fronting_show_count is not None:
        view.fronting_show_count = body.fronting_show_count

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A share view with that name already exists.",
        ) from None
    await db.refresh(view, ["members", "fields", "groups"])
    return _view_to_read(view, is_shared=shared)


@router.delete(
    "/share-views/{view_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("sharing:delete"))],
)
async def delete_share_view(
    view_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a view and, by cascade, every grant pointing at it.

    Ungated and immediate even though it destroys data: the only thing it
    destroys is exposure. Making something unreachable must never be slower
    than making it reachable.
    """
    system = await _get_user_system(user, db)
    view = await _get_view(view_id, system, db)
    await db.delete(view)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# View contents
# ---------------------------------------------------------------------------


@router.post(
    "/share-views/{view_id}/members",
    response_model=ShareViewRead,
    dependencies=[Depends(require_scope("sharing:write"))],
)
async def add_view_member(
    view_id: uuid.UUID,
    body: ShareViewMemberAdd,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareViewRead:
    system = await _get_user_system(user, db)
    view = await _get_view(view_id, system, db)

    member = (
        await db.execute(
            select(Member).where(
                Member.id == body.member_id, Member.system_id == system.id
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    shared = await view_is_shared(db, view.id)
    await _reauth_if_deferred(
        db=db,
        user=user,
        system=system,
        deferred=shared and is_exposure_safeguarded(system),
        password=body.password,
        totp_code=body.totp_code,
    )
    # Raises 400 for a never_shareable member.
    await add_member_to_view(
        db=db, system=system, view=view, member=member, already_shared=shared
    )
    await db.commit()
    await db.refresh(view, ["members", "fields", "groups"])
    return _view_to_read(view, is_shared=shared)


@router.delete(
    "/share-views/{view_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("sharing:write"))],
)
async def remove_view_member(
    view_id: uuid.UUID,
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Remove a member from a view. Immediate, ungated, idempotent."""
    system = await _get_user_system(user, db)
    view = await _get_view(view_id, system, db)
    row = (
        await db.execute(
            select(ShareViewMember).where(
                ShareViewMember.view_id == view.id,
                ShareViewMember.member_id == member_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/share-views/{view_id}/groups",
    response_model=ShareViewGroupAddResult,
    dependencies=[Depends(require_scope("sharing:write"))],
)
async def add_view_group(
    view_id: uuid.UUID,
    body: ShareViewGroupAdd,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareViewGroupAddResult:
    """Populate a view from a group's CURRENT members.

    A one-time expansion, not a live rule: members added to the group later
    are NOT pulled in automatically, because that would publish someone with
    no deliberate step and no grace window. Re-post to re-sync.
    """
    system = await _get_user_system(user, db)
    view = await _get_view(view_id, system, db)

    group = (
        await db.execute(
            select(Group).where(
                Group.id == body.group_id, Group.system_id == system.id
            )
        )
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    shared = await view_is_shared(db, view.id)
    await _reauth_if_deferred(
        db=db,
        user=user,
        system=system,
        deferred=shared and is_exposure_safeguarded(system),
        password=body.password,
        totp_code=body.totp_code,
    )
    added, skipped_secret, skipped_not_public = await expand_group_into_view(
        db=db, system=system, view=view, group_id=group.id
    )
    await db.commit()
    return ShareViewGroupAddResult(
        added=len(added),
        skipped_never_shareable=skipped_secret,
        skipped_not_public=skipped_not_public,
    )


@router.delete(
    "/share-views/{view_id}/groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("sharing:write"))],
)
async def remove_view_group(
    view_id: uuid.UUID,
    group_id: uuid.UUID,
    remove_members: bool = True,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Drop a group association.

    `remove_members` defaults to True: the members it pulled in go too. The
    privacy-favouring default, since the usual reason to remove a group is
    "these people should not be in here". Pass False to keep them as
    individually-chosen members.
    """
    system = await _get_user_system(user, db)
    view = await _get_view(view_id, system, db)

    link = (
        await db.execute(
            select(ShareViewGroup).where(
                ShareViewGroup.view_id == view.id,
                ShareViewGroup.group_id == group_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if remove_members:
        member_ids = (
            await db.execute(
                select(Member.id)
                .join(group_members, group_members.c.member_id == Member.id)
                .where(group_members.c.group_id == group_id)
            )
        ).scalars().all()
        if member_ids:
            rows = (
                await db.execute(
                    select(ShareViewMember).where(
                        ShareViewMember.view_id == view.id,
                        ShareViewMember.member_id.in_(member_ids),
                    )
                )
            ).scalars().all()
            for row in rows:
                await db.delete(row)

    await db.delete(link)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/share-views/{view_id}/fields",
    response_model=ShareViewRead,
    dependencies=[Depends(require_scope("sharing:write"))],
)
async def add_view_field(
    view_id: uuid.UUID,
    body: ShareViewFieldAdd,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareViewRead:
    system = await _get_user_system(user, db)
    view = await _get_view(view_id, system, db)

    field = (
        await db.execute(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.id == body.field_id,
                CustomFieldDefinition.system_id == system.id,
            )
        )
    ).scalar_one_or_none()
    if field is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Custom field not found"
        )

    shared = await view_is_shared(db, view.id)
    await _reauth_if_deferred(
        db=db,
        user=user,
        system=system,
        deferred=shared and is_exposure_safeguarded(system),
        password=body.password,
        totp_code=body.totp_code,
    )
    await add_field_to_view(db=db, system=system, view=view, field_id=field.id)
    await db.commit()
    await db.refresh(view, ["members", "fields", "groups"])
    return _view_to_read(view, is_shared=shared)


@router.delete(
    "/share-views/{view_id}/fields/{field_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("sharing:write"))],
)
async def remove_view_field(
    view_id: uuid.UUID,
    field_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    system = await _get_user_system(user, db)
    view = await _get_view(view_id, system, db)
    row = (
        await db.execute(
            select(ShareViewField).where(
                ShareViewField.view_id == view.id,
                ShareViewField.field_id == field_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------


@router.get("/share-grants", response_model=list[ShareGrantRead])
async def list_share_grants(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ShareGrantRead]:
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(ShareGrant)
        .where(ShareGrant.system_id == system.id)
        .order_by(ShareGrant.created_at.desc())
    )
    return [_grant_to_read(g) for g in result.scalars().all()]


@router.post(
    "/share-grants",
    response_model=ShareGrantCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("sharing:write"))],
)
async def create_share_grant(
    body: ShareGrantCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareGrantCreated:
    """Publish a view, either publicly or behind an opaque link.

    Gated on the 18+ self-declaration (inside `create_grant`) and, when the
    system has armed the profile_visibility safety category, on re-auth plus
    the grace window - the grant lands pending and the finalize job makes it
    live. For a link grant the raw token is in the response and nowhere else.
    """
    system = await _get_user_system(user, db)
    view = await _get_view(body.view_id, system, db)

    await _reauth_if_deferred(
        db=db,
        user=user,
        system=system,
        deferred=is_exposure_safeguarded(system),
        password=body.password,
        totp_code=body.totp_code,
    )

    grant, raw_token = await create_grant(
        db=db,
        system=system,
        user=user,
        view=view,
        subject_type=body.subject_type,
        note=body.note,
        expires_at=body.expires_at,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This system already has a public profile.",
        ) from None
    await db.refresh(grant)
    return ShareGrantCreated(grant=_grant_to_read(grant), token=raw_token)


@router.post(
    "/share-grants/{grant_id}/rotate",
    response_model=ShareGrantCreated,
    dependencies=[Depends(require_scope("sharing:write"))],
)
async def rotate_share_grant(
    grant_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareGrantCreated:
    """Issue a new link token; the old URL stops working at once.

    Ungated on purpose. Rotation is how someone cuts off a link that has
    spread further than intended, so it must not wait on anything.
    """
    system = await _get_user_system(user, db)
    grant = (
        await db.execute(
            select(ShareGrant).where(
                ShareGrant.id == grant_id, ShareGrant.system_id == system.id
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Share grant not found"
        )
    raw_token = rotate_grant_token(grant)
    await db.commit()
    await db.refresh(grant)
    return ShareGrantCreated(grant=_grant_to_read(grant), token=raw_token)


@router.delete(
    "/share-grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("sharing:delete"))],
)
async def revoke_share_grant(
    grant_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Go dark. Immediate, ungated, idempotent - the panic button.

    Deliberately requires no re-auth and honours no grace period: nothing,
    including the safety system itself, may slow down un-publishing.
    """
    system = await _get_user_system(user, db)
    grant = (
        await db.execute(
            select(ShareGrant).where(
                ShareGrant.id == grant_id, ShareGrant.system_id == system.id
            )
        )
    ).scalar_one_or_none()
    # 404 for a grant that is not this system's (same shape as a grant that
    # never existed - no cross-tenant existence oracle), consistent with
    # delete_share_view and rotate. Re-revoking one's own grant still 204s,
    # since the soft-revoked row is still found.
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Share grant not found"
        )
    revoke_grant(grant)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@router.get("/sharing/audit", response_model=ShareAudit)
async def sharing_audit(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareAudit:
    """"Who can currently see what", in one place.

    Lists every grant that is live or about to be, with what its view actually
    exposes. Revoked grants are omitted: this answers "what is my exposure
    right now", not "what did I ever share".
    """
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(ShareGrant, ShareView)
        .join(ShareView, ShareView.id == ShareGrant.view_id)
        .where(
            ShareGrant.system_id == system.id,
            ShareGrant.revoked_at.is_(None),
            ShareGrant.status.in_(
                [ShareGrantStatus.ACTIVE.value, ShareGrantStatus.PENDING.value]
            ),
        )
        .options(
            selectinload(ShareView.members),
            selectinload(ShareView.fields),
        )
        .order_by(ShareGrant.created_at.desc())
    )
    entries = [
        ShareAuditEntry(
            grant=_grant_to_read(grant),
            view_id=view.id,
            view_name=view.name,
            member_count=len(view.members),
            field_count=len(view.fields),
            include_bio=view.include_bio,
            include_fronting=view.include_fronting,
        )
        for grant, view in result.all()
    ]
    return ShareAudit(entries=entries)

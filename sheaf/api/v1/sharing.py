"""Owner-side management of share views and grants.

Every endpoint here is scoped to the caller's own system. The asymmetry that
runs through the whole feature shows up in the re-auth rules: anything that
EXPOSES more (creating a grant, adding a member/field/group to a view that is
already shared, turning one of that view's exposure flags on) is re-auth-gated
when the system has armed the `profile_visibility` safety category, while
anything that exposes LESS (revoking, rotating, removing, deleting, turning a
flag back off) is immediate and never gated.

Step-up and staging are two separate controls (see `visibility_step_up_required`
and `visibility_grace_days`). The category being armed demands re-auth; a grace
window on top of it also PARKS the change as pending state, which
`finalize_share_activations` makes live once the window has elapsed. With the
window at 0 (the default) an exposing change re-auths and then lands live at
once - accepted, not "refused until later".

The same asymmetry decides what an instance with its public surface switched off
still allows: everything that un-exposes or exposes nothing keeps working
(revoke, rotate, tighten, delete, and curating a view's contents), while the two
acts that would EXPOSE MORE are refused outright (`_block_new_exposure`). The
audit is never gated - dormant grants are precisely the ones an owner needs to
be able to see and revoke.

The anonymous read surface lives in a separate router. The one endpoint here
that produces public-shaped payloads is `preview_share_view`, and it produces
them for the OWNER of the view and nobody else: same projection functions, same
flags, no bypass, behind the same authentication and tenant scoping as every
other route on this router.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import (
    block_pending_deletion,
    get_current_user,
    require_scope,
)
from sheaf.config import settings
from sheaf.database import get_db
from sheaf.models.custom_field import CustomFieldDefinition
from sheaf.models.group import Group
from sheaf.models.member import Member
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
    SharePreview,
    ShareViewCreate,
    ShareViewFieldAdd,
    ShareViewGroupAdd,
    ShareViewGroupAddResult,
    ShareViewMemberAdd,
    ShareViewRead,
    ShareViewUpdate,
)
from sheaf.services.share_projection import (
    project_fronting,
    project_groups,
    project_members,
    project_relationships,
    project_system,
    projectable_fields,
    projectable_groups,
    projectable_relationships,
)
from sheaf.services.sharing import (
    EXPOSURE_FLAGS,
    add_field_to_view,
    add_member_to_view,
    create_grant,
    expand_group_into_view,
    grant_live_clause,
    revoke_grant,
    rotate_grant_token,
    suppression_reason,
    view_is_shared,
    visibility_grace_days,
    visibility_step_up_required,
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
    view_id: uuid.UUID,
    system: System,
    db: AsyncSession,
    *,
    load: bool = False,
    for_update: bool = False,
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
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    view = result.scalar_one_or_none()
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Share view not found"
        )
    return view


def _block_new_exposure(user: User) -> None:
    """Refuse an act that would EXPOSE MORE, for either reason it can be refused.

    Two conditions behind one gate, because they are the same rule seen from two
    sides: nothing new gets published while whatever would serve it is not going
    to be there.

    - The account is on its way out (`block_pending_deletion`): the owner has
      said they will not be around to manage the exposure, and the deletion
      sweep is going to remove the data underneath it.
    - The instance's public surface is switched off. The anonymous router 404s
      wholesale on that setting, so a grant minted now serves nobody today - and
      would quietly START serving the moment an operator flips the setting back,
      months later, with nobody left who remembers agreeing to it. A dormant
      grant that wakes up on somebody else's config change is exactly the
      exposure the rest of this feature is built to make impossible.

    Deliberately NOT applied to creating a view, or to adding a member, a field
    or a group to one: those expose nothing by themselves - a view nothing
    points at is a private list - so the gate belongs on publishing, which is
    the same place the safety category puts it. The pending-deletion half is
    stricter and stays where it already is on those endpoints, for its own
    reason: an account being deleted should not be building anything.

    403 rather than the 409 `block_pending_deletion` answers with, and the
    difference is the point. A pending deletion is a state the caller can change
    themselves, which is why that detail tells them how. An instance setting is
    not - only the operator can move it - so this is a refusal rather than a
    conflict to go and resolve, and it matches the 403 the per-system view cap
    already uses for "this instance will not let you have more of these".
    """
    block_pending_deletion(user)
    if not settings.public_profiles_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Public profiles and share links are turned off on this "
                "instance, so nothing new can be published. Anything already "
                "published is kept, and unpublishing still works."
            ),
        )


async def _step_up_if_required(
    *,
    db: AsyncSession,
    user: User,
    system: System,
    required: bool,
    password: str | None,
    totp_code: str | None,
) -> None:
    """Step-up auth, but only when this exposing action demands it.

    Re-auth is the price of an action that publishes something - whether it goes
    live at once or waits out a grace window first. It fires on the category
    being armed (`visibility_step_up_required`), independent of the grace period;
    staging behind a window is the caller's separate decision via
    `visibility_grace_days`.
    """
    if required:
        await verify_destructive_auth(user, system, password, totp_code, db)


def _view_to_read(view: ShareView, *, is_shared: bool) -> ShareViewRead:
    return ShareViewRead(
        id=view.id,
        name=view.name,
        include_members=view.include_members,
        include_bio=view.include_bio,
        include_fronting=view.include_fronting,
        fronting_show_count=view.fronting_show_count,
        include_relationships=view.include_relationships,
        include_groups=view.include_groups,
        member_permalinks=view.member_permalinks,
        created_at=view.created_at,
        is_shared=is_shared,
        pending_include_bio=view.pending_include_bio,
        pending_include_fronting=view.pending_include_fronting,
        pending_fronting_show_count=view.pending_fronting_show_count,
        pending_include_relationships=view.pending_include_relationships,
        pending_include_members=view.pending_include_members,
        pending_include_groups=view.pending_include_groups,
        flags_activate_at=view.flags_activate_at,
        members=[
            {
                "id": m.id,
                "member_id": m.member_id,
                "status": m.status,
                "activates_at": m.activates_at,
                "added_via_group_id": m.added_via_group_id,
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
            grant_live_clause(),
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

    That holds for the instance-level switch too: with the public surface off,
    building a view is note-taking, and refusing it would only stop somebody
    preparing for the day the operator turns the surface on. `create_share_grant`
    is where the switch bites.
    """
    block_pending_deletion(user)
    system = await _get_user_system(user, db)

    existing = (
        await db.execute(
            select(func.count(ShareView.id)).where(ShareView.system_id == system.id)
        )
    ).scalar_one()
    if existing >= settings.share_views_max:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Maximum {settings.share_views_max} share views per system. "
                "Delete one you no longer need first."
            ),
        )

    view = ShareView(
        id=uuid.uuid4(),
        system_id=system.id,
        name=body.name.strip(),
        include_members=body.include_members,
        include_bio=body.include_bio,
        include_fronting=body.include_fronting,
        fronting_show_count=body.fronting_show_count,
        include_relationships=body.include_relationships,
        include_groups=body.include_groups,
        member_permalinks=body.member_permalinks,
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

    Turning one of the exposure flags ON while the view is already shared
    exposes more, so it is deferred and re-auth-gated exactly like adding a
    member: the new value is STAGED (`pending_<flag>` + `flags_activate_at`),
    the live flag does not move, and the finalize sweep promotes it once the
    grace window has elapsed. Turning a flag off is immediate, ungated, and
    cancels any staged flip of that flag. Renaming exposes nothing and is
    always immediate.

    Loosening is also where the instance-level switch applies: with the public
    surface off, a flag turned on now would come into force under whoever is
    around when it is turned back on. Tightening is never refused.

    `member_permalinks` is handled outside that machinery on purpose. It is not
    in `EXPOSURE_FLAGS`, has no pending twin, and is applied in both directions
    the moment it is asked for, because it publishes nothing new: every member
    it gives an address to is already on the roster this view serves. Staging a
    change that reveals nobody would cost the owner a re-auth and a wait for no
    protection, and would teach them that the grace window is a formality.
    """
    system = await _get_user_system(user, db)
    view = await _get_view(view_id, system, db, load=True, for_update=True)
    shared = await view_is_shared(db, view.id)

    # Switching fronting_show_count from "hide" to "count" reveals that someone
    # not in the view is fronting, which is strictly more than before, so it
    # counts as a loosening alongside the two include_* flags.
    requested = {flag: getattr(body, flag) for flag in EXPOSURE_FLAGS}
    loosening = {
        flag
        for flag, value in requested.items()
        if value is True and not getattr(view, flag)
    }
    # Only the loosening direction is refused, and it is refused for both of
    # the reasons in `_block_new_exposure` - an account on its way out, and an
    # instance whose public surface is switched off. Turning a flag OFF has to
    # stay available to the very last minute: it is the un-exposing direction,
    # and nothing - not the safety system, not this check, not the operator's
    # setting - is allowed to stand between somebody and going dark.
    if loosening:
        _block_new_exposure(user)
    # Step-up whenever the category is armed and this loosening would actually
    # reach a reader (the view is shared). Staging behind the grace window is a
    # separate question answered by `visibility_grace_days`: with a window the
    # flip parks pending, with none it lands live once the re-auth clears.
    exposing = bool(loosening) and shared
    step_up = exposing and visibility_step_up_required(system)
    await _step_up_if_required(
        db=db,
        user=user,
        system=system,
        required=step_up,
        password=body.password,
        totp_code=body.totp_code,
    )
    grace = visibility_grace_days(system)
    stage = exposing and grace > 0

    if body.name is not None:
        view.name = body.name.strip()
    if body.member_permalinks is not None:
        view.member_permalinks = body.member_permalinks

    activates_at = datetime.now(UTC) + timedelta(days=grace)
    for flag, value in requested.items():
        if value is None:
            continue
        if stage and flag in loosening:
            setattr(view, f"pending_{flag}", True)
            # One clock for the whole view; a later loosening restarts it.
            view.flags_activate_at = activates_at
        else:
            # Everything else - tightening, an unshared or unsafeguarded view,
            # a no-op repeat - lands now and drops anything staged for that
            # flag, so switching a pending flip back off cancels it outright.
            setattr(view, flag, value)
            setattr(view, f"pending_{flag}", None)
    if all(getattr(view, f"pending_{flag}") is None for flag in EXPOSURE_FLAGS):
        view.flags_activate_at = None

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


@router.get("/share-views/{view_id}/preview", response_model=SharePreview)
async def preview_share_view(
    view_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SharePreview:
    """This view, exactly as a visitor would receive it.

    The design doc asks owners to be able to check their own page before anyone
    else does, and the only version of that worth shipping is one backed by the
    real projection. Two invariants hold it to that, and both are enforced by
    construction rather than by remembering:

    1. **Same functions, same flags.** Every section comes from the
       `share_projection` call the anonymous router makes for that section, with
       this view passed in unmodified. There is no flag bypass and nothing extra
       in the payload: a section the view does not serve is `null` here, which
       is what its 404 means there. If the projection changes, the preview
       changes with it, so the two cannot drift into disagreeing - and a preview
       that disagreed with the page would be worse than no preview, because the
       owner would trust it.

    2. **Publication is not a precondition.** It deliberately does NOT require a
       grant, or the instance's public switch, or anything else that decides
       whether the page is currently reachable. Looking at what you are about to
       publish BEFORE you publish it is the entire point; a preview you can only
       see once it is already live is a report, not a preview.

    Which leaves the honesty problem those two create together: with no grant
    check, a preview would happily render a full page for a system whose public
    surface is suppressed account-wide. So `suppressed` carries the same coarse
    reason `sharing/audit` reports, from the same helper as the
    `profile_serving_clause` gate the anonymous resolvers apply. The sections
    stay populated - "what would visitors see" and "is anyone getting this right
    now" are separate questions and the client says both - but the preview never
    claims to be live when it is not.

    Owner-scoped like everything else on this router (`sharing:read`, and
    `_get_view` 404s another tenant's id), and on the ordinary authenticated
    limits rather than the public bucket: this is one of the owner's own reads,
    not anonymous traffic, and putting it in the public per-IP bucket would let
    an owner previewing their page eat the quota their visitors share.
    """
    system = await _get_user_system(user, db)
    view = await _get_view(view_id, system, db)

    return SharePreview(
        system=await project_system(db, view, system),
        # Each of these mirrors one anonymous endpoint's gate, in the same
        # order it applies there: the flag decides whether the section exists
        # at all, and the projection decides what is in it.
        members=(
            await project_members(db, view, owner_id=system.user_id)
            if view.include_members
            else None
        ),
        fronting=(
            await project_fronting(db, view, system)
            if view.include_fronting
            else None
        ),
        relationships=(
            await project_relationships(db, view)
            if view.include_relationships
            else None
        ),
        groups=(
            await project_groups(db, view, owner_id=system.user_id)
            if view.include_groups
            else None
        ),
        suppressed=suppression_reason(system, user),
    )


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
    """Add one member to a view's selection.

    Not gated on the instance's public switch, same as the rest of the view
    contents: putting somebody in a view publishes nothing until a grant serves
    it, and with the surface off no grant can be created at all. The grace
    window still applies when the view is already shared, because "already
    shared" is about this view, not about the instance.
    """
    block_pending_deletion(user)
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
    await _step_up_if_required(
        db=db,
        user=user,
        system=system,
        required=shared and visibility_step_up_required(system),
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

    Ungated on the instance switch for the same reason as `add_view_member`:
    selection is not publication.
    """
    block_pending_deletion(user)
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
    await _step_up_if_required(
        db=db,
        user=user,
        system=system,
        required=shared and visibility_step_up_required(system),
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

    `remove_members` defaults to True: the members THIS GROUP PUT HERE go too.
    The privacy-favouring default, since the usual reason to remove a group is
    "these people should not be in here". Pass False to keep them as
    individually-chosen members.

    Removal goes by `ShareViewMember.added_via_group_id` - the group expansion
    that actually created each row - and not by the group's current roster.
    Those are different sets, and using the roster over-removed in two ordinary
    cases: a member the owner ALSO picked by hand, and a member an overlapping
    group brought in, were both pulled out of the view although this group was
    not the reason they were in it. Detaching a group may only undo what
    attaching it did.

    The snapshot semantic is untouched in the other direction too, and this is
    the case that reads backwards until you look at what the row says: somebody
    who has since LEFT the group is still removed, because their row is stamped
    with this expansion, so this group is exactly why they are in the view.
    Leaving the group never moved them out (that is the whole point - group
    membership does not drive exposure), so detaching the thing that put them
    there is the act that does.

    Rows whose stamp is NULL are never touched. That covers hand-picked members,
    rows created before the column existed, and rows whose group has since been
    deleted (the FK nulls the stamp rather than cascading, so deleting a group
    cannot silently rewrite anybody's view).
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
        rows = (
            await db.execute(
                select(ShareViewMember).where(
                    ShareViewMember.view_id == view.id,
                    ShareViewMember.added_via_group_id == group_id,
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
    """Select a custom field into a view.

    Ungated on the instance switch, same as the member and group selections:
    nothing here reaches a reader until a grant serves the view.
    """
    block_pending_deletion(user)
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
    await _step_up_if_required(
        db=db,
        user=user,
        system=system,
        required=shared and visibility_step_up_required(system),
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

    Refused outright while the account is scheduled for deletion, and while the
    instance's public surface is switched off - see `_block_new_exposure` for
    both. Revoking and rotating stay open under either condition: going dark is
    never blocked.
    """
    _block_new_exposure(user)
    system = await _get_user_system(user, db)
    view = await _get_view(body.view_id, system, db)

    await _step_up_if_required(
        db=db,
        user=user,
        system=system,
        required=visibility_step_up_required(system),
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
    including the safety system itself, may slow down un-publishing. That
    includes the instance's public switch - with the surface off this grant
    serves nobody today, but it is exactly the grant that would start serving
    again if the setting came back, so revoking it has to stay possible while
    the surface is off. Same for rotation, which kills the old link outright.
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
    right now", not "what did I ever share". An expired one stays listed even
    though it now exposes nothing - the owner set that expiry and should see
    that it lapsed rather than watch the entry vanish; `expires_at` is on every
    entry and the client labels it.

    Never gated on the instance's public switch, and that is load-bearing
    rather than an oversight: with the surface off these grants are dormant,
    not gone, and an owner who cannot see them cannot revoke the ones they do
    not want waking up if an operator turns it back on. The one thing worse
    than an audit that over-reports is an audit that disappears.

    `profile_suppressed` sits above the entries and answers the question they
    cannot: whether anything below is actually reaching anybody. A suppressed
    account keeps every grant and every count exactly as it was - suppression
    does not revoke - so without this field the audit would confidently describe
    an exposure that is currently 404ing, which is the one way an audit can be
    worse than none at all.
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
        # Only the members are eager-loaded now: the field count comes from
        # `projectable_fields`, which queries the definitions with the ceiling
        # applied rather than counting selection rows off the view.
        .options(selectinload(ShareView.members))
        .order_by(ShareGrant.created_at.desc())
    )
    entries = []
    for grant, view in result.all():
        # Counted through the projection's own helpers rather than recomputed
        # here: an audit that disagreed with what visitors actually get would
        # be worse than no audit at all.
        edges = await projectable_relationships(db, view)
        groups = await projectable_groups(db, view)
        fields = await projectable_fields(db, view)
        entries.append(
            ShareAuditEntry(
                grant=_grant_to_read(grant),
                view_id=view.id,
                view_name=view.name,
                # Deliberately the curated count, not a served count: with the
                # roster off it is still the number of people this view is set
                # up to show, and `include_members` beside it says whether they
                # are being shown. Zeroing it would read as "your curation is
                # gone" for a flag that destroyed nothing.
                member_count=len(view.members),
                # The SERVED count, not the curated one, and the deliberate
                # difference from `member_count` above. A member held back by
                # their own privacy is visible as such right beside this
                # number - the roster flag is reported here and the sharing
                # screen badges every member the ceiling is holding - so the
                # curated count reads as curation rather than as a claim about
                # what visitors get. A field has no such companion: the audit
                # entry says nothing about which definitions are public, so a
                # count of selection rows would be the only number on the
                # screen and it would over-report the exposure. Counting
                # through the projection's own filter is also what stops the
                # two from drifting.
                field_count=len(fields),
                include_members=view.include_members,
                include_bio=view.include_bio,
                include_fronting=view.include_fronting,
                include_relationships=view.include_relationships,
                include_groups=view.include_groups,
                member_permalinks=view.member_permalinks,
                relationship_count=len(edges),
                group_count=len(groups),
            )
        )
    return ShareAudit(
        entries=entries,
        profile_suppressed=suppression_reason(system, user),
    )

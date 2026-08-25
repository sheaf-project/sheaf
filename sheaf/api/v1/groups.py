import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.files import owned_description_urls
from sheaf.models.group import Group
from sheaf.models.member import Member
from sheaf.models.pending_action import PendingActionType
from sheaf.models.system import PrivacyLevel, System
from sheaf.models.user import User
from sheaf.observability.metrics import groups_created_total
from sheaf.schemas.group import GroupCreate, GroupMemberUpdate, GroupRead, GroupUpdate
from sheaf.schemas.member import MemberDeleteConfirm, MemberRead
from sheaf.services.members import decrypt_member_for_read
from sheaf.services.sharing import (
    group_raise_exposes,
    visibility_grace_days,
    visibility_step_up_required,
)
from sheaf.services.system_safety import (
    is_safeguarded,
    pending_finalize_after_by_target,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/groups", tags=["groups"])

# Hard cap on nesting depth. The cycle check already keeps the graph a DAG;
# this stops a pathologically deep chain (root -> ... -> leaf) from being
# built. 8 levels is far more than any real system needs.
MAX_GROUP_DEPTH = 8


async def _depth_to_root(
    start_id: uuid.UUID, system: System, db: AsyncSession
) -> int:
    """Number of groups from `start_id` up to a root, inclusive.

    A root (parent_id is None) returns 1. Defensive against a malformed
    cycle (bounded by `seen`) even though the write paths prevent them.
    """
    depth = 0
    current: uuid.UUID | None = start_id
    seen: set[uuid.UUID] = set()
    while current is not None and current not in seen:
        seen.add(current)
        depth += 1
        result = await db.execute(
            select(Group.parent_id).where(
                Group.id == current, Group.system_id == system.id
            )
        )
        current = result.scalar_one_or_none()
    return depth


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")
    return system


async def _get_own_group(
    group_id: uuid.UUID,
    system: System,
    db: AsyncSession,
    *,
    for_update: bool = False,
) -> Group:
    """Fetch one group of this system, 404ing if it is not.

    `for_update` matches `update_member` and the relationship-edge PATCH: two
    concurrent privacy writes on one group must not interleave into a state
    where the live level and the staged level disagree about which way the
    owner was going.
    """
    stmt = select(Group).where(Group.id == group_id, Group.system_id == system.id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
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
    groups = list(result.scalars().all())
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.GROUP_DELETE
    )
    out: list[GroupRead] = []
    for g in groups:
        gr = GroupRead.model_validate(g)
        gr.pending_delete_at = pending.get(g.id)
        out.append(gr)
    return out


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
        if await _depth_to_root(body.parent_id, system, db) >= MAX_GROUP_DEPTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Group nesting cannot exceed {MAX_GROUP_DEPTH} levels",
            )

    fields = body.model_dump()
    # Step-up credentials are not group columns; drop them before the row is
    # built so they can never be persisted.
    password = fields.pop("password", None)
    totp_code = fields.pop("totp_code", None)

    # Drop embedded refs to another account's storage keys, exactly as the
    # member and system write handlers do. Groups were the gap: nothing here
    # ran an ownership pass, so a foreign /v1/files/{key} pasted into a group
    # description was stored verbatim and re-signed into a live serve URL on
    # every read - a cross-tenant read of somebody else's upload, and one that
    # keeps working after they have deleted or un-shared the original. Enforced
    # at the handler because this is where the authenticated user exists; the
    # schema validator that runs `normalize_description_urls` has no request
    # context and cannot know whose keys these are.
    fields["description"] = owned_description_urls(
        fields.get("description"), user.id
    )

    # Creating a group straight to `public` exposes exactly what raising an
    # existing one does, so it runs the SAME check and gets the same treatment:
    # step-up now when the category is armed and it would actually serve, then a
    # grace window (if set) stages the raise while the group is born private,
    # else it is simply born public. Without this, "delete it and add it back
    # public" would walk around the PATCH gate entirely.
    if (
        fields.get("privacy") == PrivacyLevel.PUBLIC
        and visibility_step_up_required(system)
        and await group_raise_exposes(db, system)
    ):
        await verify_destructive_auth(user, system, password, totp_code, db)
        grace = visibility_grace_days(system)
        if grace > 0:
            fields["privacy"] = PrivacyLevel.PRIVATE
            fields["pending_privacy"] = PrivacyLevel.PUBLIC
            fields["privacy_activates_at"] = datetime.now(UTC) + timedelta(
                days=grace
            )

    group = Group(system_id=system.id, **fields)
    db.add(group)
    await db.commit()
    groups_created_total.inc()
    await db.refresh(group)
    return group


@router.get("/{group_id}", response_model=GroupRead)
async def get_group(
    group_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    group = await _get_own_group(group_id, system, db)
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.GROUP_DELETE
    )
    gr = GroupRead.model_validate(group)
    gr.pending_delete_at = pending.get(group.id)
    return gr


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
    """Edit a group, including moving it up or down the privacy ladder.

    Raising a group to `public` EXPOSES it, but only when something would
    actually serve it - see `group_raise_exposes`. When it would, this behaves
    exactly like raising a member or an edge to public: re-auth now, and the
    raise itself waits out the grace window as `pending_privacy` +
    `privacy_activates_at` while the live level stays where it was.

    Every other direction is instant and ungated. Lowering is the un-exposing
    direction and nothing may slow it down, and ANY such change lands on top of
    whatever was staged rather than queueing behind it - setting private, or
    friends, while a public raise is pending cancels that raise outright. The
    last thing the owner asked for wins, and it wins at its own gate. private ->
    friends is ungated for the same reason it is on members and edges: the
    friends tier is parked and every grant that exists today is public-tier, so
    it exposes nobody. When friends lands, this check and
    `share_projection.projectable_groups` have to become audience-aware
    together.
    """
    system = await _get_user_system(user, db)
    group = await _get_own_group(group_id, system, db, for_update=True)
    update_data = body.model_dump(exclude_unset=True)
    # Step-up credentials are not group columns; drop them before anything
    # iterates the update so they can never be persisted.
    password = update_data.pop("password", None)
    totp_code = update_data.pop("totp_code", None)

    # Same ownership pass as create_group; see the note there.
    if "description" in update_data:
        update_data["description"] = owned_description_urls(
            update_data["description"], user.id
        )

    requested_privacy = update_data.pop("privacy", None)
    exposes = False
    if (
        requested_privacy == PrivacyLevel.PUBLIC
        and group.privacy != PrivacyLevel.PUBLIC
        and visibility_step_up_required(system)
    ):
        exposes = await group_raise_exposes(db, system, group.id)

    if exposes:
        await verify_destructive_auth(user, system, password, totp_code, db)
        grace = visibility_grace_days(system)
        if grace > 0:
            group.pending_privacy = PrivacyLevel.PUBLIC
            group.privacy_activates_at = datetime.now(UTC) + timedelta(days=grace)
        else:
            group.privacy = PrivacyLevel.PUBLIC
            group.pending_privacy = None
            group.privacy_activates_at = None
    elif requested_privacy is not None:
        group.privacy = requested_privacy
        group.pending_privacy = None
        group.privacy_activates_at = None

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
            # `visited` now holds this group plus the new parent's full
            # ancestor chain, so its size is the depth this group would sit
            # at. (Does not account for the moved subtree's own height; the
            # cap is a guard against abuse, not an exact invariant.)
            if len(visited) > MAX_GROUP_DEPTH:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Group nesting cannot exceed {MAX_GROUP_DEPTH} levels",
                )

    for key, value in update_data.items():
        setattr(group, key, value)
    await db.commit()
    await db.refresh(group)
    return group


@router.delete(
    "/{group_id}",
    dependencies=[Depends(require_scope("groups:delete"))],
)
async def delete_group(
    group_id: uuid.UUID,
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
    group = await _get_own_group(group_id, system, db)

    if is_safeguarded(system, PendingActionType.GROUP_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.GROUP_DELETE,
            target_id=group.id,
            target_label=group.name,
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

    await db.delete(group)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    return [decrypt_member_for_read(m, user.id) for m in group.members]


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
    await db.commit()
    return [decrypt_member_for_read(m, user.id) for m in members]

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.files import resolve_avatar_url
from sheaf.models.group import Group
from sheaf.models.member import Member
from sheaf.models.relationship import (
    GroupRelationship,
    MemberRelationship,
    RelationshipSymmetry,
    RelationshipType,
)
from sheaf.models.system import PrivacyLevel, System
from sheaf.models.user import User
from sheaf.schemas.relationship import (
    RelationshipEdgeCreate,
    RelationshipEdgeRead,
    RelationshipEdgeUpdate,
    RelationshipFromViewpoint,
    RelationshipGraph,
    RelationshipGraphEdge,
    RelationshipGraphNode,
    RelationshipTypeCreate,
    RelationshipTypeRead,
    RelationshipTypeUpdate,
)
from sheaf.services.members import member_plaintext
from sheaf.services.relationships import (
    canonicalize_pair,
    endpoint_labels,
    resolve_label,
)
from sheaf.services.sharing import (
    is_exposure_safeguarded,
    relationship_raise_exposes,
)
from sheaf.services.system_safety import verify_destructive_auth

router = APIRouter(tags=["relationships"])


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )
    return system


def _is_undirected(symmetry: RelationshipSymmetry, mutual: bool) -> bool:
    return symmetry == RelationshipSymmetry.SYMMETRIC or (
        symmetry == RelationshipSymmetry.EITHER and mutual
    )


async def _types_by_id(
    db: AsyncSession, type_ids: set[uuid.UUID]
) -> dict[uuid.UUID, RelationshipType]:
    if not type_ids:
        return {}
    rows = await db.execute(
        select(RelationshipType).where(RelationshipType.id.in_(type_ids))
    )
    return {t.id: t for t in rows.scalars().all()}


# ---------------------------------------------------------------------------
# Relationship types (the per-system vocabulary)
# ---------------------------------------------------------------------------


@router.get("/relationship-types", response_model=list[RelationshipTypeRead])
async def list_relationship_types(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    rows = await db.execute(
        select(RelationshipType)
        .where(RelationshipType.system_id == system.id)
        .order_by(RelationshipType.name)
    )
    return list(rows.scalars().all())


@router.post(
    "/relationship-types",
    response_model=RelationshipTypeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("relationships:write"))],
)
async def create_relationship_type(
    body: RelationshipTypeCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    rt = RelationshipType(system_id=system.id, **body.model_dump())
    db.add(rt)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A relationship type with that name already exists",
        ) from e
    await db.refresh(rt)
    return rt


async def _get_type_in_system(
    type_id: uuid.UUID, system: System, db: AsyncSession
) -> RelationshipType:
    row = await db.execute(
        select(RelationshipType).where(
            RelationshipType.id == type_id,
            RelationshipType.system_id == system.id,
        )
    )
    rt = row.scalar_one_or_none()
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relationship type not found",
        )
    return rt


@router.get(
    "/relationship-types/{type_id}", response_model=RelationshipTypeRead
)
async def get_relationship_type(
    type_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    return await _get_type_in_system(type_id, system, db)


@router.patch(
    "/relationship-types/{type_id}",
    response_model=RelationshipTypeRead,
    dependencies=[Depends(require_scope("relationships:write"))],
)
async def update_relationship_type(
    type_id: uuid.UUID,
    body: RelationshipTypeUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    rt = await _get_type_in_system(type_id, system, db)
    update = body.model_dump(exclude_unset=True)
    if rt.symmetry == RelationshipSymmetry.SYMMETRIC:
        # reverse_label is meaningless for symmetric types; ignore any attempt.
        update.pop("reverse_label", None)
    elif "reverse_label" in update and (
        not update["reverse_label"] or not update["reverse_label"].strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="reverse_label cannot be empty for directional / either types",
        )
    for key, value in update.items():
        setattr(rt, key, value)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A relationship type with that name already exists",
        ) from e
    await db.refresh(rt)
    return rt


@router.delete(
    "/relationship-types/{type_id}",
    dependencies=[Depends(require_scope("relationships:delete"))],
)
async def delete_relationship_type(
    type_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    # Deleting a type cascades its edges (DB FK). Low-stakes + reversible by
    # re-adding, so no System Safety gate; the web client confirms first.
    system = await _get_user_system(user, db)
    rt = await _get_type_in_system(type_id, system, db)
    await db.delete(rt)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Edges. Member and group endpoints share the shape; only the node table and
# ORM model differ. One canonical row is stored; the per-node GET derives the
# inverse via the shared engine.
# ---------------------------------------------------------------------------


async def _node_ids_in_system(
    db: AsyncSession,
    model: type[Member] | type[Group],
    system: System,
    ids: set[uuid.UUID],
) -> set[uuid.UUID]:
    rows = await db.execute(
        select(model.id).where(model.id.in_(ids), model.system_id == system.id)
    )
    return set(rows.scalars().all())


async def _create_edge(
    body: RelationshipEdgeCreate,
    *,
    node_model: type[Member] | type[Group],
    edge_model: type[MemberRelationship] | type[GroupRelationship],
    node_label: str,
    gated: bool,
    system: System,
    db: AsyncSession,
    user: User,
):
    if body.source_id == body.target_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A {node_label} cannot have a relationship with itself",
        )
    present = await _node_ids_in_system(
        db, node_model, system, {body.source_id, body.target_id}
    )
    if len(present) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"source and target must both be {node_label}s in your system",
        )
    rt = await db.execute(
        select(RelationshipType).where(
            RelationshipType.id == body.relationship_type_id,
            RelationshipType.system_id == system.id,
        )
    )
    rtype = rt.scalar_one_or_none()
    if rtype is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown relationship type",
        )
    src, tgt = canonicalize_pair(rtype.symmetry, body.source_id, body.target_id)
    # `mutual` only means anything for `either` types; normalise it off otherwise.
    mutual = body.mutual and rtype.symmetry == RelationshipSymmetry.EITHER

    # Creating an edge straight to `public` exposes exactly what raising an
    # existing one to `public` does, so it runs the SAME check and gets the same
    # treatment: step-up now, and the edge is born private with the raise staged
    # behind the grace window. Without this, "delete it and add it back public"
    # would walk around the PATCH gate entirely. Group edges pass gated=False -
    # nothing projects them, so there is no exposure to defer.
    extra: dict = {}
    visibility = body.visibility
    if (
        gated
        and visibility == PrivacyLevel.PUBLIC
        and is_exposure_safeguarded(system)
        and await relationship_raise_exposes(
            db, system, source_id=src, target_id=tgt
        )
    ):
        await verify_destructive_auth(
            user, system, body.password, body.totp_code, db
        )
        visibility = PrivacyLevel.PRIVATE
        extra = {
            "pending_visibility": PrivacyLevel.PUBLIC,
            "visibility_activates_at": datetime.now(UTC)
            + timedelta(days=system.safety_grace_period_days),
        }

    edge = edge_model(
        system_id=system.id,
        source_id=src,
        target_id=tgt,
        relationship_type_id=rtype.id,
        mutual=mutual,
        visibility=visibility,
        **extra,
    )
    db.add(edge)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That relationship already exists",
        ) from e
    await db.refresh(edge)
    return edge


async def _node_relationships(
    node_id: uuid.UUID,
    *,
    node_model: type[Member] | type[Group],
    edge_model: type[MemberRelationship] | type[GroupRelationship],
    system: System,
    db: AsyncSession,
) -> list[RelationshipFromViewpoint]:
    present = await _node_ids_in_system(db, node_model, system, {node_id})
    if not present:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
        )
    rows = await db.execute(
        select(edge_model).where(
            edge_model.system_id == system.id,
            or_(
                edge_model.source_id == node_id,
                edge_model.target_id == node_id,
            ),
        )
    )
    edges = list(rows.scalars().all())
    types = await _types_by_id(db, {e.relationship_type_id for e in edges})
    out: list[RelationshipFromViewpoint] = []
    for e in edges:
        t = types.get(e.relationship_type_id)
        if t is None:
            continue
        label, direction = resolve_label(
            symmetry=t.symmetry,
            forward_label=t.forward_label,
            reverse_label=t.reverse_label,
            mutual=e.mutual,
            source_id=e.source_id,
            viewpoint_id=node_id,
        )
        other_id = e.target_id if e.source_id == node_id else e.source_id
        out.append(
            RelationshipFromViewpoint(
                id=e.id,
                relationship_type_id=t.id,
                type_name=t.name,
                other_id=other_id,
                label=label,
                direction=direction,
                mutual=e.mutual,
                visibility=e.visibility,
                # Group edges never stage a raise, so they have no such
                # columns; both read as null there.
                pending_visibility=getattr(e, "pending_visibility", None),
                visibility_activates_at=getattr(
                    e, "visibility_activates_at", None
                ),
            )
        )
    return out


async def _get_edge_for_update(
    edge_id: uuid.UUID,
    *,
    edge_model: type[MemberRelationship] | type[GroupRelationship],
    system: System,
    db: AsyncSession,
):
    """Fetch one edge of this system, locked for the duration of the request.

    Same 404 for "no such edge" and "not yours", so an id from another tenant
    cannot be probed. `FOR UPDATE` matches `update_member`: two concurrent
    privacy writes on one edge must not interleave into a state where the live
    level and the staged level disagree about which way the owner was going.
    """
    row = await db.execute(
        select(edge_model)
        .where(edge_model.id == edge_id, edge_model.system_id == system.id)
        .with_for_update()
    )
    edge = row.scalar_one_or_none()
    if edge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found"
        )
    return edge


async def _delete_edge(
    edge_id: uuid.UUID,
    *,
    edge_model: type[MemberRelationship] | type[GroupRelationship],
    system: System,
    db: AsyncSession,
) -> Response:
    row = await db.execute(
        select(edge_model).where(
            edge_model.id == edge_id, edge_model.system_id == system.id
        )
    )
    edge = row.scalar_one_or_none()
    if edge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found"
        )
    await db.delete(edge)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Member edges ---


@router.get(
    "/members/{member_id}/relationships",
    response_model=list[RelationshipFromViewpoint],
)
async def list_member_relationships(
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    return await _node_relationships(
        member_id,
        node_model=Member,
        edge_model=MemberRelationship,
        system=system,
        db=db,
    )


@router.post(
    "/member-relationships",
    response_model=RelationshipEdgeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("relationships:write"))],
)
async def create_member_relationship(
    body: RelationshipEdgeCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    return await _create_edge(
        body,
        node_model=Member,
        edge_model=MemberRelationship,
        node_label="member",
        gated=True,
        system=system,
        db=db,
        user=user,
    )


@router.patch(
    "/member-relationships/{edge_id}",
    response_model=RelationshipEdgeRead,
    dependencies=[Depends(require_scope("relationships:write"))],
)
async def update_member_relationship(
    edge_id: uuid.UUID,
    body: RelationshipEdgeUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Move one member edge up or down the privacy ladder.

    Raising an edge to `public` EXPOSES it, but only if the whole chain that
    would draw it is already in place - see `relationship_raise_exposes`. When
    it is, this behaves exactly like raising a member to public: re-auth now,
    and the raise itself waits out the grace window as
    `pending_visibility` + `visibility_activates_at` while the live level stays
    where it was.

    Every other direction is instant and ungated. Lowering is the un-exposing
    direction and nothing may slow it down, and ANY such change lands on top of
    whatever was staged rather than queueing behind it - setting private, or
    friends, while a public raise is pending cancels that raise outright. The
    last thing the owner asked for wins, and it wins at its own gate. private ->
    friends is ungated for the same reason it is on members: the friends tier is
    parked and every grant that exists today is public-tier, so it exposes
    nobody. When friends lands, this check and
    `share_projection.projectable_relationships` have to become audience-aware
    together.
    """
    system = await _get_user_system(user, db)
    update_data = body.model_dump(exclude_unset=True)
    # Step-up credentials are not edge columns; drop them before anything
    # iterates the update so they can never be persisted.
    password = update_data.pop("password", None)
    totp_code = update_data.pop("totp_code", None)

    edge = await _get_edge_for_update(
        edge_id, edge_model=MemberRelationship, system=system, db=db
    )
    requested = update_data.get("visibility")
    if requested is None:
        return edge

    deferred = False
    if (
        requested == PrivacyLevel.PUBLIC
        and edge.visibility != PrivacyLevel.PUBLIC
        and is_exposure_safeguarded(system)
    ):
        deferred = await relationship_raise_exposes(
            db, system, source_id=edge.source_id, target_id=edge.target_id
        )

    if deferred:
        await verify_destructive_auth(user, system, password, totp_code, db)
        edge.pending_visibility = PrivacyLevel.PUBLIC
        edge.visibility_activates_at = datetime.now(UTC) + timedelta(
            days=system.safety_grace_period_days
        )
    else:
        edge.visibility = requested
        edge.pending_visibility = None
        edge.visibility_activates_at = None

    await db.commit()
    await db.refresh(edge)
    return edge


@router.delete(
    "/member-relationships/{edge_id}",
    dependencies=[Depends(require_scope("relationships:delete"))],
)
async def delete_member_relationship(
    edge_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    system = await _get_user_system(user, db)
    return await _delete_edge(
        edge_id, edge_model=MemberRelationship, system=system, db=db
    )


# --- Group edges ---


@router.get(
    "/groups/{group_id}/relationships",
    response_model=list[RelationshipFromViewpoint],
)
async def list_group_relationships(
    group_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    return await _node_relationships(
        group_id,
        node_model=Group,
        edge_model=GroupRelationship,
        system=system,
        db=db,
    )


@router.post(
    "/group-relationships",
    response_model=RelationshipEdgeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("relationships:write"))],
)
async def create_group_relationship(
    body: RelationshipEdgeCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    return await _create_edge(
        body,
        node_model=Group,
        edge_model=GroupRelationship,
        node_label="group",
        gated=False,
        system=system,
        db=db,
        user=user,
    )


@router.patch(
    "/group-relationships/{edge_id}",
    response_model=RelationshipEdgeRead,
    dependencies=[Depends(require_scope("relationships:write"))],
)
async def update_group_relationship(
    edge_id: uuid.UUID,
    body: RelationshipEdgeUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set one group edge's privacy level. Always instant, never gated.

    Deliberately unlike the member endpoint above: no share view flag reaches
    group edges and `share_projection` never queries that table, so a group edge
    marked public is still visible to nobody but its owner. There is no exposure
    to defer, and inventing a grace window for a change that exposes nothing
    would only teach people the gate is theatre. The level is stored so it is
    already correct if group edges are ever projected - at which point this
    endpoint needs the same gate the member one has, added BEFORE the
    projection, not after.
    """
    system = await _get_user_system(user, db)
    update_data = body.model_dump(exclude_unset=True)
    # Not columns here either; dropped for the same reason.
    update_data.pop("password", None)
    update_data.pop("totp_code", None)

    edge = await _get_edge_for_update(
        edge_id, edge_model=GroupRelationship, system=system, db=db
    )
    requested = update_data.get("visibility")
    if requested is None:
        return edge

    edge.visibility = requested
    await db.commit()
    await db.refresh(edge)
    return edge


@router.delete(
    "/group-relationships/{edge_id}",
    dependencies=[Depends(require_scope("relationships:delete"))],
)
async def delete_group_relationship(
    edge_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    system = await _get_user_system(user, db)
    return await _delete_edge(
        edge_id, edge_model=GroupRelationship, system=system, db=db
    )


# ---------------------------------------------------------------------------
# Whole-graph fetch for the viewer
# ---------------------------------------------------------------------------


@router.get("/relationships/graph", response_model=RelationshipGraph)
async def relationship_graph(
    scope: Literal["members", "groups"] = "members",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)

    nodes: list[RelationshipGraphNode] = []
    if scope == "members":
        rows = await db.execute(
            select(Member).where(Member.system_id == system.id)
        )
        for m in rows.scalars().all():
            name_pt, _ = member_plaintext(m)
            nodes.append(
                RelationshipGraphNode(
                    id=m.id,
                    name=m.display_name or name_pt,
                    avatar_url=resolve_avatar_url(m.avatar_url),
                    color=m.color,
                )
            )
        edge_model: type[MemberRelationship] | type[GroupRelationship] = (
            MemberRelationship
        )
    else:
        rows = await db.execute(
            select(Group).where(Group.system_id == system.id)
        )
        for g in rows.scalars().all():
            nodes.append(
                RelationshipGraphNode(id=g.id, name=g.name, color=g.color)
            )
        edge_model = GroupRelationship

    edge_rows = await db.execute(
        select(edge_model).where(edge_model.system_id == system.id)
    )
    edges_list = list(edge_rows.scalars().all())
    types = await _types_by_id(db, {e.relationship_type_id for e in edges_list})
    edges: list[RelationshipGraphEdge] = []
    for e in edges_list:
        t = types.get(e.relationship_type_id)
        if t is None:
            continue
        src_label, tgt_label = endpoint_labels(
            symmetry=t.symmetry,
            forward_label=t.forward_label,
            reverse_label=t.reverse_label,
            mutual=e.mutual,
        )
        edges.append(
            RelationshipGraphEdge(
                id=e.id,
                source_id=e.source_id,
                target_id=e.target_id,
                relationship_type_id=t.id,
                type_name=t.name,
                source_label=src_label,
                target_label=tgt_label,
                mutual=e.mutual,
                directed=not _is_undirected(t.symmetry, e.mutual),
            )
        )
    return RelationshipGraph(nodes=nodes, edges=edges)

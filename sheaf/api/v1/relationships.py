import uuid
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
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.relationship import (
    RelationshipEdgeCreate,
    RelationshipEdgeRead,
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
    system: System,
    db: AsyncSession,
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
    edge = edge_model(
        system_id=system.id,
        source_id=src,
        target_id=tgt,
        relationship_type_id=rtype.id,
        mutual=mutual,
        visibility=body.visibility,
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
            )
        )
    return out


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
        system=system,
        db=db,
    )


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
        system=system,
        db=db,
    )


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

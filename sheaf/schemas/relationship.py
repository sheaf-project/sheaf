import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from sheaf.models.relationship import RelationshipSymmetry, RelationshipVisibility


class RelationshipTypeCreate(BaseModel):
    name: str = Field(max_length=100)
    symmetry: RelationshipSymmetry
    forward_label: str = Field(max_length=100)
    reverse_label: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _reverse_label_rules(self) -> "RelationshipTypeCreate":
        # Symmetric types read one label from both ends, so reverse_label is
        # meaningless - drop it. Directional / either types need it.
        if self.symmetry == RelationshipSymmetry.SYMMETRIC:
            self.reverse_label = None
        elif not self.reverse_label or not self.reverse_label.strip():
            raise ValueError(
                "reverse_label is required for directional and either types"
            )
        return self


class RelationshipTypeUpdate(BaseModel):
    # `symmetry` is intentionally immutable after creation: existing edges'
    # stored orientation and `mutual` semantics depend on it (a symmetric type's
    # edges are canonicalised with arbitrary source/target order), so flipping it
    # would silently corrupt them. Name + labels are freely editable.
    name: str | None = Field(default=None, max_length=100)
    forward_label: str | None = Field(default=None, max_length=100)
    reverse_label: str | None = Field(default=None, max_length=100)

    @field_validator("name", "forward_label")
    @classmethod
    def _reject_explicit_null(cls, v):
        # These back NOT-NULL columns; `null` only exists so model_fields_set
        # can tell "omitted" from "supplied" for PATCH. Reject an explicit null.
        if v is None:
            raise ValueError("cannot be null")
        return v


class RelationshipTypeRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    name: str
    symmetry: RelationshipSymmetry
    forward_label: str
    reverse_label: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RelationshipEdgeCreate(BaseModel):
    """Create one relationship edge. `source_id`/`target_id` are member ids for
    the member endpoint and group ids for the group endpoint. For directional /
    either types source is the forward-label endpoint; for symmetric types order
    does not matter (it is canonicalised server-side)."""

    source_id: uuid.UUID
    target_id: uuid.UUID
    relationship_type_id: uuid.UUID
    mutual: bool = False
    visibility: RelationshipVisibility = RelationshipVisibility.PRIVATE


class RelationshipEdgeRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    target_id: uuid.UUID
    relationship_type_id: uuid.UUID
    mutual: bool
    visibility: RelationshipVisibility
    created_at: datetime

    model_config = {"from_attributes": True}


# Direction as read from one endpoint's viewpoint (see services/relationships.py).
Direction = Literal["none", "outgoing", "incoming"]


class RelationshipFromViewpoint(BaseModel):
    """One edge as it reads from a single member/group's perspective, used by
    GET /members/{id}/relationships and /groups/{id}/relationships. A canonical
    A<->B row surfaces in both A's and B's lists, each with the label + `other`
    endpoint resolved for that viewer."""

    id: uuid.UUID
    relationship_type_id: uuid.UUID
    type_name: str
    other_id: uuid.UUID
    label: str
    direction: Direction
    mutual: bool
    visibility: RelationshipVisibility


class RelationshipGraphNode(BaseModel):
    id: uuid.UUID
    name: str
    avatar_url: str | None = None
    color: str | None = None


class RelationshipGraphEdge(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    target_id: uuid.UUID
    relationship_type_id: uuid.UUID
    type_name: str
    source_label: str
    target_label: str
    mutual: bool
    # False for symmetric types and for mutual either-edges (undirected).
    directed: bool


class RelationshipGraph(BaseModel):
    nodes: list[RelationshipGraphNode]
    edges: list[RelationshipGraphEdge]

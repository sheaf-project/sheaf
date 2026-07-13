import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class RelationshipSymmetry(enum.StrEnum):
    """How a relationship type reads from each endpoint.

    - SYMMETRIC: one label, unordered; both ends read `forward_label`
      (e.g. partner).
    - DIRECTIONAL: source reads `forward_label`, target reads `reverse_label`
      (e.g. parent/child); the edge is inherently ordered.
    - EITHER: the type supports both; a given edge is directional unless its
      `mutual` flag is set, in which case both ends read `forward_label`
      (e.g. protector -> protectee, or mutual protectors). This is the whole
      reason directionality lives on the TYPE, not on separate types per
      direction (avoids the "make a second type for the inverse" trap).
    """

    SYMMETRIC = "symmetric"
    DIRECTIONAL = "directional"
    EITHER = "either"


class RelationshipVisibility(enum.StrEnum):
    """Reserved for the future ACL/public-profiles primitive.

    Only PRIVATE exists in v1 and it is stored-but-not-enforced (all reads are
    owner-only today). The column exists now so ACL can light relationships up
    later without a migration on a live table.
    """

    PRIVATE = "private"


class RelationshipType(UUIDMixin, TimestampMixin, Base):
    """A per-system, user-defined kind of relationship (partner, protector,
    parent/child, ...). Starts empty per system; the web editor seeds new rows
    from client-side preset templates. Shared vocabulary for both member and
    group edges."""

    __tablename__ = "relationship_types"
    __table_args__ = (
        UniqueConstraint(
            "system_id", "name", name="uq_relationship_types_system_name"
        ),
    )

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    symmetry: Mapped[RelationshipSymmetry] = mapped_column(
        Enum(RelationshipSymmetry, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    # The single label for symmetric types and the source-side label otherwise.
    forward_label: Mapped[str] = mapped_column(String(100), nullable=False)
    # The target-side label for directional / either types; unused (null) for
    # symmetric. Required at the schema layer when symmetry != symmetric.
    reverse_label: Mapped[str | None] = mapped_column(String(100), nullable=True)


# ---------------------------------------------------------------------------
# Edge tables. Member<->member and group<->group share the RelationshipType
# vocabulary and the same shape. Exactly ONE canonical row is stored per
# relationship; the inverse is derived at render time by the shared engine
# (services/relationships.py). See that module for the label-resolution logic.
#
# `source_id` has TYPE-DEPENDENT meaning and this is the subtlest thing here:
#   - directional / either edges: source is the `forward_label` endpoint
#     (e.g. the parent, or the protector); order is load-bearing, preserve it.
#   - symmetric edges: direction is meaningless, so the API/importer store the
#     lexicographically-smaller uuid as source purely for stable dedup/render.
#
# Uniqueness is over the UNORDERED pair per type (you cannot have both A->B and
# B->A of one type). A plain UniqueConstraint(source,target,type) does NOT
# enforce that, so the guarantee is a FUNCTIONAL unique index on
# (system_id, relationship_type_id, least(source,target), greatest(source,target))
# created in the migration (not expressible cleanly in __table_args__). The
# no-self-edge CheckConstraint below is declarative and mirrored in the migration.
# ---------------------------------------------------------------------------


class MemberRelationship(UUIDMixin, Base):
    __tablename__ = "member_relationships"
    __table_args__ = (
        CheckConstraint(
            "source_id <> target_id", name="ck_member_rel_no_self"
        ),
    )

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relationship_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("relationship_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Only meaningful for `either` types: when true, both ends read forward_label.
    mutual: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    visibility: Mapped[RelationshipVisibility] = mapped_column(
        Enum(
            RelationshipVisibility, values_callable=lambda e: [m.value for m in e]
        ),
        default=RelationshipVisibility.PRIVATE,
        server_default=RelationshipVisibility.PRIVATE.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GroupRelationship(UUIDMixin, Base):
    __tablename__ = "group_relationships"
    __table_args__ = (
        CheckConstraint(
            "source_id <> target_id", name="ck_group_rel_no_self"
        ),
    )

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relationship_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("relationship_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mutual: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    visibility: Mapped[RelationshipVisibility] = mapped_column(
        Enum(
            RelationshipVisibility, values_callable=lambda e: [m.value for m in e]
        ),
        default=RelationshipVisibility.PRIVATE,
        server_default=RelationshipVisibility.PRIVATE.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

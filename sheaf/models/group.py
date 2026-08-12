import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin
from sheaf.models.member import group_members
from sheaf.models.system import PrivacyLevel


class Group(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "groups"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)

    # Per-group privacy ceiling, in the same vocabulary a member and a member
    # edge already speak - it is the same question, so it gets the same three
    # levels rather than a parallel one that could drift. `private` (the
    # default) is owner-only; `public` is the only level the public projection
    # serves, and even then only through a view whose `include_groups` flag is
    # on. Unlike an edge, the group itself IS the payload: its name,
    # description and colour are published as the owner wrote them. Its roster
    # is not a second decision - the projection intersects it with the members
    # the view already shows, so a public group can never name somebody the
    # view was not already naming.
    privacy: Mapped[PrivacyLevel] = mapped_column(
        Enum(PrivacyLevel, values_callable=lambda e: [m.value for m in e]),
        default=PrivacyLevel.PRIVATE,
        server_default=PrivacyLevel.PRIVATE.value,
        nullable=False,
    )
    # Staged raise, the twin of `MemberRelationship.pending_visibility`.
    # Publishing a group that would ACTUALLY show is a loosening, so the new
    # level parks here, `privacy` above stays put, and the share finalizer
    # promotes it once `privacy_activates_at` passes. Setting a lower (or
    # equal) level meanwhile cancels the staged raise outright: going dark
    # always wins and never waits.
    pending_privacy: Mapped[PrivacyLevel | None] = mapped_column(
        Enum(PrivacyLevel, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    )
    privacy_activates_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Self-referential FK for subsystem nesting
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    system: Mapped["System"] = relationship(back_populates="groups")
    members: Mapped[list["Member"]] = relationship(
        secondary=group_members, back_populates="groups"
    )
    # No delete-orphan / delete cascade: the DB FK is ON DELETE SET NULL,
    # so deleting a parent group un-nests its children rather than
    # destroying them. The ORM cascade must match that, or an ORM-issued
    # delete and a raw-SQL delete would behave differently.
    children: Mapped[list["Group"]] = relationship(back_populates="parent")
    parent: Mapped["Group | None"] = relationship(
        back_populates="children", remote_side="Group.id"
    )

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin
from sheaf.models.member import group_members


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
    children: Mapped[list["Group"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped["Group | None"] = relationship(
        back_populates="children", remote_side="Group.id"
    )

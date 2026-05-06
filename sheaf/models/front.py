import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, UUIDMixin
from sheaf.models.member import front_members


class Front(UUIDMixin, Base):
    __tablename__ = "fronts"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Optional free-text per-fronting-period annotation, e.g. "during a job
    # interview" or "panic attack at the wedding". Encrypted at rest because
    # it's exactly the kind of contextual narrative that the field-level
    # encryption model is meant to protect, matching the precedent set by
    # bios and journal entries.
    custom_status: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    system: Mapped["System"] = relationship(back_populates="fronts")
    members: Mapped[list["Member"]] = relationship(
        secondary=front_members, back_populates="fronts"
    )

    __table_args__ = (
        # Composite index for the most common query pattern:
        # "get fronts for system X ordered by time" and for retention pruning
        Index("ix_fronts_system_started", "system_id", "started_at"),
        # Fast lookup for "who is currently fronting" (ended_at IS NULL)
        Index("ix_fronts_system_current", "system_id", "ended_at"),
    )

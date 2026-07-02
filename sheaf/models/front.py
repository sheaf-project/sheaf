import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func
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

    # Row-creation timestamp, distinct from the front's real-world `started_at`.
    # This is *when the row landed in this database* (created or imported), so
    # it is the correct key for any age-based handling: an imported historical
    # front gets a fresh created_at, not its old start date. The prior retention
    # job keyed off `started_at` and so deleted just-imported history; see
    # ../sheaf-design-docs/front-history-retention-and-limits.md.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

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
        # Free-tier retention sweep: system_id IN (...) AND created_at < cutoff
        # AND ended_at IS NOT NULL AND ended_at < cutoff. Neither index above
        # covers created_at, so the predicate seq-scanned; this composite lets
        # it index-scan the closed-and-old rows.
        Index(
            "ix_fronts_system_ended_created", "system_id", "ended_at", "created_at"
        ),
        # A closed front can't end before it starts. The edit endpoint already
        # checks this, but creation/import paths build Front rows directly and
        # bypassed it - this is the mechanical backstop so a mis-ordered front
        # can never be persisted (and then become un-editable). Added NOT VALID
        # in the migration so pre-existing rows aren't retroactively rejected.
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_fronts_ended_after_started",
        ),
    )

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, UUIDMixin


class RetentionTrimStatus(StrEnum):
    PENDING = "pending"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class RetentionTrimNotice(UUIDMixin, Base):
    """A pending notice that revision-history retention will be trimmed.

    Created when something — currently only a tier downgrade — would force
    the user's stored revisions below their previous effective caps.
    The retention GC job uses pre-downgrade caps until effective_at passes,
    then applies the new (lower) caps and marks the notice completed.
    """

    __tablename__ = "retention_trim_notices"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Snapshot of the tier transition that prompted the notice. from_tier is
    # used to reconstruct the old (higher) caps during the grace window.
    from_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    to_tier: Mapped[str] = mapped_column(String(32), nullable=False)

    reason: Mapped[str] = mapped_column(
        String(64), nullable=False, default="tier_downgrade"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RetentionTrimStatus.PENDING
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_retention_trim_notices_due",
            "status",
            "effective_at",
        ),
    )

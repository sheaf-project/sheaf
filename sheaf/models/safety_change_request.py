import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, UUIDMixin


class SafetyChangeStatus(StrEnum):
    PENDING = "pending"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class SafetyChangeRequest(UUIDMixin, Base):
    """A queued loosening of System Safety settings, held during the grace period.

    Tightening changes apply immediately; only changes that reduce protection
    (shorter grace, weaker auth tier, disabling categories) land here.
    """

    __tablename__ = "safety_change_requests"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    finalize_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    changes: Mapped[dict] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SafetyChangeStatus.PENDING
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    system: Mapped["System"] = relationship()

    __table_args__ = (
        Index(
            "ix_safety_change_requests_due",
            "system_id",
            "status",
            "finalize_after",
        ),
    )

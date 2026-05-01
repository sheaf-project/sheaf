import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, UUIDMixin


class NotificationOutboxRow(UUIDMixin, Base):
    """One row per (event, channel). Per-member resolution happens at dispatch
    time, not at enqueue, so owner config changes between enqueue and dispatch
    take effect."""

    __tablename__ = "notification_outbox"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notification_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deliver_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_notification_outbox_due",
            "deliver_after",
            postgresql_where="delivered_at IS NULL",
        ),
        Index(
            "ix_notification_outbox_channel_due",
            "channel_id",
            "deliver_after",
            postgresql_where="delivered_at IS NULL",
        ),
    )

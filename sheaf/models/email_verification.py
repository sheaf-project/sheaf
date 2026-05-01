"""Reserved for the email destination type. Created in v1's migration so the
email branch is purely additive when it lands later. Not used by v1 code."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, UUIDMixin


class EmailVerification(UUIDMixin, Base):
    __tablename__ = "email_verifications"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notification_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    confirm_token_hash: Mapped[str] = mapped_column(String, nullable=False)
    block_token_hash: Mapped[str] = mapped_column(String, nullable=False)
    confirm_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consumed_action: Mapped[str | None] = mapped_column(String(8), nullable=True)

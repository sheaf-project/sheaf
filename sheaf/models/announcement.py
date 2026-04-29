import uuid
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class AnnouncementSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ServerAnnouncement(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "server_announcements"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=AnnouncementSeverity.INFO,
    )
    dismissible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    visible_while_logged_out: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    starts_at: Mapped[None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
    expires_at: Mapped[None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )

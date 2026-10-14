import uuid
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class AnnouncementSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AnnouncementPlatform(StrEnum):
    """The clients an announcement can be aimed at.

    Mirrors the interactive client families in
    sheaf.observability.client_family, minus `other`, which nobody would
    target on purpose. A client asks for its own platform's announcements;
    an announcement with no platforms is for everyone.
    """

    WEB = "web"
    ANDROID = "android"
    IOS = "ios"
    WATCH = "watch"


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
    # Empty means "every client". Non-empty means only clients that ask for
    # one of these platforms see it; a client that asks for none sees only
    # the empty ones. Values are AnnouncementPlatform.
    platforms: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), nullable=False, default=list, server_default="{}"
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

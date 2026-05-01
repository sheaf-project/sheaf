import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class WatchToken(UUIDMixin, TimestampMixin, Base):
    """A subscription primitive: grants a recipient the right to receive
    notifications about a system. No data access, no co-fronting visibility,
    no commenting. Purely unidirectional pings.

    A token groups one or more notification channels (different destinations,
    different filters) under a single owner-controlled label like "Mara".
    """

    __tablename__ = "watch_tokens"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
    )

    label: Mapped[str | None] = mapped_column(String(120), nullable=True)

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    system: Mapped["System"] = relationship()
    channels: Mapped[list["NotificationChannel"]] = relationship(
        back_populates="watch_token", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index(
            "ix_watch_tokens_system_active",
            "system_id",
            postgresql_where="revoked_at IS NULL",
        ),
    )

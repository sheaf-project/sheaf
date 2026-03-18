import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin
from sheaf.models.member import member_tags


class Tag(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tags"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(50), nullable=False)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)

    # Relationships
    system: Mapped["System"] = relationship(back_populates="tags")
    members: Mapped[list["Member"]] = relationship(
        secondary=member_tags, back_populates="tags"
    )

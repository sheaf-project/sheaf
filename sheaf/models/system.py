import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class PrivacyLevel(enum.StrEnum):
    PUBLIC = "public"
    FRIENDS = "friends"
    PRIVATE = "private"


class DateFormat(enum.StrEnum):
    DMY = "dmy"  # 19/03/2026
    MDY = "mdy"  # 03/19/2026
    YMD = "ymd"  # 2026-03-19


class DeleteConfirmation(enum.StrEnum):
    NONE = "none"
    PASSWORD = "password"
    TOTP = "totp"
    BOTH = "both"


class System(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "systems"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tag: Mapped[str | None] = mapped_column(String(8), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    privacy: Mapped[PrivacyLevel] = mapped_column(
        Enum(PrivacyLevel, values_callable=lambda e: [m.value for m in e]),
        default=PrivacyLevel.PRIVATE,
        nullable=False,
    )
    delete_confirmation: Mapped[DeleteConfirmation] = mapped_column(
        Enum(DeleteConfirmation, values_callable=lambda e: [m.value for m in e]),
        default=DeleteConfirmation.NONE,
        nullable=False,
    )
    date_format: Mapped[DateFormat] = mapped_column(
        Enum(DateFormat, values_callable=lambda e: [m.value for m in e]),
        default=DateFormat.YMD,
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="system")
    members: Mapped[list["Member"]] = relationship(
        back_populates="system", cascade="all, delete-orphan"
    )
    groups: Mapped[list["Group"]] = relationship(
        back_populates="system", cascade="all, delete-orphan"
    )
    tags: Mapped[list["Tag"]] = relationship(
        back_populates="system", cascade="all, delete-orphan"
    )
    fronts: Mapped[list["Front"]] = relationship(
        back_populates="system", cascade="all, delete-orphan"
    )
    custom_field_definitions: Mapped[list["CustomFieldDefinition"]] = relationship(
        back_populates="system", cascade="all, delete-orphan"
    )

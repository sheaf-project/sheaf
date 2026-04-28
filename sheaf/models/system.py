import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
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


# Name is historical. Now used across System Safety as the auth tier for all
# safeguarded destructive actions, not just delete confirmation.
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
    # Historical name. Now the auth tier for all safeguarded destructive
    # actions under System Safety (members, groups, tags, fields, fronts).
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
    # When True, creating a new front automatically ends all currently open fronts.
    replace_fronts_default: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    # System Safety — grace period + per-category toggles for destructive actions.
    # 0 days means no grace; paired with all category toggles off by default.
    safety_grace_period_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    safety_applies_to_members: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    safety_applies_to_groups: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    safety_applies_to_tags: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    safety_applies_to_fields: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    safety_applies_to_fronts: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    safety_applies_to_journals: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    safety_applies_to_images: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Revision-history retention overrides. NULL = use the tier-default cap;
    # a concrete value must be <= the tier max (validated at write time).
    # Reductions route through SafetyChangeRequest (asymmetric loosening).
    journal_max_revisions: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    journal_max_revision_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True
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

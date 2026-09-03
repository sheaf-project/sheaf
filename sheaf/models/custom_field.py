import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin
from sheaf.models.system import PrivacyLevel


class FieldType(enum.StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    SELECT = "select"
    MULTISELECT = "multiselect"


class CustomFieldDefinition(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "custom_field_definitions"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    field_type: Mapped[FieldType] = mapped_column(
        Enum(FieldType, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    options: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # The definition's exposure CEILING, in the same three-level vocabulary a
    # member, a group and a member edge already speak - and with the same
    # meaning: permission, not a promise. A field still only appears where a
    # view was told to show it. It applies to the field for EVERY member: there
    # is deliberately no per-member-per-field level, because a setting that
    # says "public except for these three" is one the owner has to keep right
    # forever, and the day they forget is the day it outs somebody.
    privacy: Mapped[PrivacyLevel] = mapped_column(
        Enum(PrivacyLevel, values_callable=lambda e: [m.value for m in e]),
        default=PrivacyLevel.PRIVATE,
        nullable=False,
    )
    # A raise waiting out the System Safety grace window: `privacy` above is
    # still the truth until `privacy_activates_at` passes, at which point the
    # sharing finalizer copies `pending_privacy` over it. Both null when
    # nothing is staged. Same pair, same reasons, as `Group.pending_privacy`.
    pending_privacy: Mapped[PrivacyLevel | None] = mapped_column(
        Enum(PrivacyLevel, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    )
    privacy_activates_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    system: Mapped["System"] = relationship(back_populates="custom_field_definitions")
    values: Mapped[list["CustomFieldValue"]] = relationship(
        back_populates="field_definition", cascade="all, delete-orphan"
    )


class CustomFieldValue(UUIDMixin, Base):
    __tablename__ = "custom_field_values"
    # One value per (field, member). The constraint also indexes field_id
    # (leftmost column); member_id gets its own index below.
    __table_args__ = (
        UniqueConstraint(
            "field_id", "member_id", name="uq_custom_field_values_field_member"
        ),
    )

    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_field_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True, info={"encrypted": True})

    # Relationships
    field_definition: Mapped["CustomFieldDefinition"] = relationship(back_populates="values")
    member: Mapped["Member"] = relationship(back_populates="custom_field_values")

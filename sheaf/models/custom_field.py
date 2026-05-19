import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String, UniqueConstraint
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
    privacy: Mapped[PrivacyLevel] = mapped_column(
        Enum(PrivacyLevel, values_callable=lambda e: [m.value for m in e]),
        default=PrivacyLevel.PRIVATE,
        nullable=False,
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

    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    field_definition: Mapped["CustomFieldDefinition"] = relationship(back_populates="values")
    member: Mapped["Member"] = relationship(back_populates="custom_field_values")

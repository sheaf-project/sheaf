import uuid

from sqlalchemy import Column, Enum, ForeignKey, String, Table, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin
from sheaf.models.system import PrivacyLevel

# Many-to-many: members <-> fronts (co-fronting)
front_members = Table(
    "front_members",
    Base.metadata,
    Column(
        "front_id", UUID(as_uuid=True),
        ForeignKey("fronts.id", ondelete="CASCADE"), primary_key=True,
    ),
    Column(
        "member_id", UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"), primary_key=True,
    ),
)

# Many-to-many: members <-> groups
group_members = Table(
    "group_members",
    Base.metadata,
    Column(
        "group_id", UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True,
    ),
    Column(
        "member_id", UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"), primary_key=True,
    ),
)

# Many-to-many: members <-> tags
member_tags = Table(
    "member_tags",
    Base.metadata,
    Column(
        "tag_id", UUID(as_uuid=True),
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True,
    ),
    Column(
        "member_id", UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"), primary_key=True,
    ),
)


class Member(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "members"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pronouns: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # Stored as "MM-DD" or "YYYY-MM-DD" to support year-optional birthdays
    birthday: Mapped[str | None] = mapped_column(String(10), nullable=True)
    privacy: Mapped[PrivacyLevel] = mapped_column(
        Enum(PrivacyLevel, values_callable=lambda e: [m.value for m in e]),
        default=PrivacyLevel.PRIVATE,
        nullable=False,
    )

    # Relationships
    system: Mapped["System"] = relationship(back_populates="members")
    fronts: Mapped[list["Front"]] = relationship(
        secondary=front_members, back_populates="members"
    )
    groups: Mapped[list["Group"]] = relationship(
        secondary=group_members, back_populates="members"
    )
    tags: Mapped[list["Tag"]] = relationship(
        secondary=member_tags, back_populates="members"
    )
    custom_field_values: Mapped[list["CustomFieldValue"]] = relationship(
        back_populates="member", cascade="all, delete-orphan"
    )

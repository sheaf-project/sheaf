import uuid

from sqlalchemy import Boolean, Column, Enum, ForeignKey, String, Table, Text
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

    # Encrypted at application level — store ciphertext.
    # Length is generous to accommodate the base64-encoded
    # nonce+ciphertext+tag for a 100-char plaintext.
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Blind index on the *plaintext* name (keyed HMAC-SHA-256, normalised).
    # Used for exact-match lookups within a system (e.g. autocomplete dedup).
    # Not unique — duplicate names within a system are allowed.
    name_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, server_default=""
    )
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Encrypted at application level — store ciphertext.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pronouns: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # Stored as "MM-DD" or "YYYY-MM-DD" to support year-optional birthdays
    birthday: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # PluralKit member HID (5-7 lowercase letters/digits). Stored for users who
    # cross-reference between Sheaf and PluralKit; populated by the PK import,
    # editable manually. Not unique within a system; we don't validate against
    # PluralKit's namespace ourselves.
    pluralkit_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Short visual identifier (single emoji or a few characters). Surfaced
    # alongside or instead of the avatar fallback in compact lists. Optional.
    emoji: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Marks a Member as a "custom front" — a non-counting fronting entity
    # (e.g. "Asleep", "Away", "Lost time"). Custom fronts behave like members
    # for fronting/groups/notifications but are excluded from member headcount
    # statistics and listed separately in the members UI.
    is_custom_front: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    privacy: Mapped[PrivacyLevel] = mapped_column(
        Enum(PrivacyLevel, values_callable=lambda e: [m.value for m in e]),
        default=PrivacyLevel.PRIVATE,
        nullable=False,
    )

    # Free-text scratchpad note. Encrypted at rest like description.
    # Deliberately lightweight: no revisions, no System Safety protection,
    # no sub-records — overwriting clears the previous content with no
    # history. For "trigger list / fav drink / current med doses" type
    # quick reference. Soft-capped at ~5kb plaintext at the schema layer.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

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

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class JournalEntry(UUIDMixin, TimestampMixin, Base):
    """A journal entry — per-member (member_id set) or system-wide (member_id null).

    Markdown-bodied. Authorship is the snapshot of fronting members at create
    time; falls back to the user account when no one was fronting.
    """

    __tablename__ = "journal_entries"

    # No standalone index — the (system_id, created_at) composite below
    # already serves system_id-only lookups via its leftmost column.
    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Encrypted at application level — store ciphertext.
    # title and body are encrypted; image_keys stays plaintext so orphan
    # cleanup and read-time URL rewrites don't require key access.
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # v1 only honors "system". "member_private" and "public" are reserved
    # for forward compatibility and rejected by the API schema.
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="system", server_default="system"
    )

    # Account-level fallback author when no one was fronting at write time.
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Frozen snapshot of fronting members at create time.
    author_member_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    author_member_names: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # Storage keys referenced by image embeds in the body.
    # Pre-extracted at write so orphan cleanup is a fast set lookup.
    image_keys: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    system: Mapped["System"] = relationship()
    member: Mapped["Member | None"] = relationship()

    __table_args__ = (
        Index(
            "ix_journal_entries_system_created",
            "system_id",
            "created_at",
        ),
        Index(
            "ix_journal_entries_system_member_created",
            "system_id",
            "member_id",
            "created_at",
        ),
    )

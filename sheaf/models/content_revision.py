import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, UUIDMixin


# Polymorphic — covers any markdown-bodied content with revision history.
# Currently: journal entries and member bios. Extending to system bio etc.
# is a code-only change (add a value here, write at the relevant edit site).
class ContentRevisionTarget(StrEnum):
    JOURNAL_ENTRY = "journal_entry"
    MEMBER_BIO = "member_bio"


class ContentRevision(UUIDMixin, Base):
    """A previous version of a markdown-bodied content field.

    Polymorphic: target_type + target_id identify the row whose content was
    superseded. No DB-level FK on target_id because of the polymorphic shape;
    target deletion sweeps revisions at the application layer.

    Stores the *outgoing* (now-superseded) content. The current content lives
    on the target row itself, so reads don't need to JOIN here.
    """

    __tablename__ = "content_revisions"

    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Frozen snapshot of fronting members at edit time.
    editor_member_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    editor_member_names: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # Captured content as it was before the edit that produced this revision.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    image_keys: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_content_revisions_target",
            "target_type",
            "target_id",
            "created_at",
        ),
        Index("ix_content_revisions_created", "created_at"),
        Index("ix_content_revisions_user", "user_id"),
    )

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, UUIDMixin


class PendingActionType(StrEnum):
    MEMBER_DELETE = "member_delete"
    GROUP_DELETE = "group_delete"
    TAG_DELETE = "tag_delete"
    FIELD_DELETE = "field_delete"
    FRONT_DELETE = "front_delete"
    JOURNAL_DELETE = "journal_delete"
    IMAGE_DELETE = "image_delete"
    REVISION_UNPIN = "revision_unpin"
    WATCH_TOKEN_REVOKE = "watch_token_revoke"
    CHANNEL_DELETE = "channel_delete"
    REMINDER_DELETE = "reminder_delete"
    POLL_DELETE = "poll_delete"
    MESSAGE_DELETE = "message_delete"
    MESSAGE_THREAD_DELETE = "message_thread_delete"


class PendingActionStatus(StrEnum):
    PENDING = "pending"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    ERRORED = "errored"


class PendingAction(UUIDMixin, Base):
    __tablename__ = "pending_actions"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
    )

    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Encrypted at rest: this holds decrypted user content (member names,
    # journal titles, poll questions, message previews) that would otherwise
    # land in any DB dump taken during the grace window. Text because
    # ciphertext is longer than the 200-char plaintext bound. Written via
    # encrypt() in queue_pending_action; decrypted defensively on read.
    target_label: Mapped[str] = mapped_column(Text, nullable=False)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    finalize_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Snapshot of who was fronting when the action was requested.
    # Frozen - members may be deleted or front composition may change before finalization.
    # ids are opaque UUIDs (not sensitive) and stay JSONB.
    fronting_member_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Encrypted at rest for the same reason as target_label: these are
    # decrypted member names. Stored as encrypt(json.dumps(list_of_names))
    # in a Text column. No server_default - the app sets the value on every
    # write and the encrypting migration backfills existing rows.
    fronting_member_names: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=PendingActionStatus.PENDING,
        server_default=PendingActionStatus.PENDING.value,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    system: Mapped["System"] = relationship()

    __table_args__ = (
        Index(
            "ix_pending_actions_due",
            "system_id",
            "status",
            "finalize_after",
        ),
    )

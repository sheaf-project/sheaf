"""Board messages.

Two-board model: a system-wide global board, and a per-member wall on
each member. Any logged-in session for the system can post and edit
messages on either board — there is no per-member auth in v1, so the
SP-style "everyone can do everything to everyone's posts" model
applies. Audit history is captured by the same revision history as
journals + bios (polymorphic `content_revisions` with
`target_type="message"`), and the existing System Safety + auto-pin
machinery wraps the destructive paths.

Replies use a simple chain: `parent_message_id` is whatever was
clicked Reply on, the UI renders one-level "Replying to X" backlinks
rather than a deep tree. Deleting a parent leaves replies in place
with the parent shown as `[deleted]`; "delete thread" is a separate
operation that cascades.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class BoardKind(enum.StrEnum):
    SYSTEM = "system"   # Global system board
    MEMBER = "member"   # A specific member's wall


class Message(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    board_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    # board_member_id is the recipient member for member walls. NULL for
    # system-board messages. ON DELETE CASCADE — if a member is deleted,
    # their wall goes with them.
    board_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # The member the post is attributed to. NULL when the original author
    # has been deleted. The renderer shows "[deleted member]" in that case.
    author_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Single-level reply pointer. UI renders flat with a "Replying to X"
    # backlink. parent_message_id may itself be a reply — we follow the
    # chain on render but don't impose a tree structure.
    parent_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Body is markdown, encrypted at rest. Soft cap at 5000 plaintext
    # characters in the schema layer.
    body: Mapped[str] = mapped_column(Text, nullable=False, info={"encrypted": True})

    # Soft delete: set when a message is removed. Reads filter on
    # `deleted_at IS NULL`. Hard delete happens via System Safety queue
    # finalization or downstream sweeps.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    parent: Mapped[Message | None] = relationship(
        remote_side="Message.id", lazy="raise"
    )

    __table_args__ = (
        # Most common query: "all live messages on board X newest first".
        Index(
            "ix_messages_board_created",
            "system_id",
            "board_kind",
            "board_member_id",
            "created_at",
        ),
    )


class MessageReadState(UUIDMixin, Base):
    """Per-member, per-board last-seen marker for the in-app unread badge
    + the on-front-start prompt.

    A row exists per (member, board_kind, board_member_id) the moment a
    member views a board. The `last_seen_at` is updated on each view; new
    messages with `created_at > last_seen_at` count as unread for that
    member's prompt.

    Note: this is per-MEMBER, not per-account. The point is the
    on-front-start prompt: "Alice fronted, here's what's new for her
    since she last fronted". Two members fronting at different times
    each get their own state.
    """

    __tablename__ = "message_read_state"

    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    board_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    # NULL when board_kind == 'system'.
    board_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=True,
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_message_read_state_lookup",
            "member_id",
            "board_kind",
            "board_member_id",
            unique=True,
            # board_member_id is NULL for the system board. Without this,
            # Postgres treats those NULLs as distinct and the unique index
            # fails to dedupe system-board read-state rows, letting the
            # get-or-create race create duplicates. NULLS NOT DISTINCT
            # (PG15+) closes that gap.
            postgresql_nulls_not_distinct=True,
        ),
    )

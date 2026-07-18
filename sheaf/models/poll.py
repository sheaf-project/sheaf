"""Polls.

A small voting surface for system-internal decision-making. Headmates
cast votes "as" a specific member, gated on that member being part of
the current front at vote-time (basic safeguard against one headmate
silently voting on behalf of others). Every cast/change/withdraw
appends a row to poll_vote_events, including a frozen snapshot of who
was fronting and which member the vote was attributed to. The audit
log remains intelligible after a member rename or delete.

Polls have a creation-time deadline that cannot be moved (manual close
would be abusable without member-level auth, which is out of scope for
v1). After the deadline they become read-only; after retention_days
post-close, the cleanup job deletes them via cascade.

Question, description, and option text are encrypted at rest, matching
the precedent set by member descriptions / journal entries / reminder
content.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class PollKind(enum.StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"


class PollResultsVisibility(enum.StrEnum):
    LIVE = "live"
    END_ONLY = "end_only"


class PollVoteAction(enum.StrEnum):
    CAST = "cast"
    CHANGE = "change"
    WITHDRAW = "withdraw"


class Poll(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "polls"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Encrypted at rest.
    question: Mapped[str] = mapped_column(Text, nullable=False, info={"encrypted": True})
    description: Mapped[str | None] = mapped_column(Text, nullable=True, info={"encrypted": True})

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    results_visibility: Mapped[str] = mapped_column(String(16), nullable=False)

    closes_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)

    # When false (default), custom-front members (Asleep / Away / etc.) can't
    # cast votes — they're system states, not decision-making entities. Set
    # true if you actually want a "what should we do when we wake up" poll
    # cast by Asleep.
    include_custom_fronts: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    # When true, only members in the current front at vote time may cast
    # or change a vote. Default false matches the journals model (any
    # member can author / vote regardless of front state). Set true on
    # creation for decisions the system wants the active fronters to own
    # ("what to wear today") rather than opening to absent members.
    restrict_voting_to_fronters: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )

    options: Mapped[list[PollOption]] = relationship(
        back_populates="poll",
        cascade="all, delete-orphan",
        order_by="PollOption.position",
    )
    votes: Mapped[list[PollVote]] = relationship(
        back_populates="poll",
        cascade="all, delete-orphan",
    )
    events: Mapped[list[PollVoteEvent]] = relationship(
        back_populates="poll",
        cascade="all, delete-orphan",
        order_by="PollVoteEvent.created_at",
    )


class PollOption(UUIDMixin, Base):
    __tablename__ = "poll_options"

    poll_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("polls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Encrypted at rest.
    text: Mapped[str] = mapped_column(Text, nullable=False, info={"encrypted": True})
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    poll: Mapped[Poll] = relationship(back_populates="options")

    __table_args__ = (
        UniqueConstraint("poll_id", "position", name="uq_poll_option_position"),
    )


class PollVote(UUIDMixin, Base):
    """Current vote per (poll, voted_as_member). Replaced on change,
    deleted on withdraw. Latest state only — full history lives in
    poll_vote_events."""

    __tablename__ = "poll_votes"

    poll_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("polls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    voted_as_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
    )
    option_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    poll: Mapped[Poll] = relationship(back_populates="votes")

    __table_args__ = (
        UniqueConstraint(
            "poll_id", "voted_as_member_id", name="uq_poll_vote_per_member"
        ),
    )


class PollVoteEvent(UUIDMixin, Base):
    """Append-only audit log of vote activity on a poll.

    One row per cast / change / withdraw. Member names and option labels
    aren't snapshotted: name changes propagate retroactively, and option
    text is stable for the life of the poll (creation-only).
    """

    __tablename__ = "poll_vote_events"

    poll_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("polls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    voted_as_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    option_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    fronting_member_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    poll: Mapped[Poll] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_poll_vote_events_poll_created", "poll_id", "created_at"),
    )

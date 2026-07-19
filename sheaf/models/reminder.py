"""Reminder data model.

Two trigger types share one row, discriminated by `trigger_type`:

- automated: front-event-driven. Set `trigger_member_id` (or null for
  "any front change") and `delay_seconds`. The notifications event hook
  enqueues a delayed send when a matching event lands.
- repeated: schedule-driven. Use the structured `schedule_*` fields for
  daily/weekly/monthly cadence, or `cron_expression` for the "Advanced"
  power-user mode (takes precedence when set).

Repeated reminders can be scope-limited to specific members. When the
schedule fires and no scoped member is currently fronting, the reminder
either drops silently (`digest_when_absent=False`) or queues a row in
`reminder_pending` (default). On the next front-start of any scoped
member, the queue is drained as a single digest notification per channel.
The pending queue caps at 5 entries per reminder; oldest is dropped on
overflow.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, TimestampMixin, UUIDMixin

# M2M: reminders <-> members (scoping for repeated reminders)
reminder_scope_members = Table(
    "reminder_scope_members",
    Base.metadata,
    Column(
        "reminder_id",
        UUID(as_uuid=True),
        ForeignKey("reminders.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "member_id",
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class Reminder(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "reminders"

    system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notification_channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # title + body are encrypted at rest (matching the encryption discipline
    # used for member descriptions and journal entries — both are free-text
    # user content).
    title: Mapped[str] = mapped_column(Text, nullable=False, info={"encrypted": True})
    body: Mapped[str | None] = mapped_column(Text, nullable=True, info={"encrypted": True})

    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default="true"
    )

    # "automated" or "repeated"
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # --- automated (front-event-triggered) ---
    # null trigger_member_id with trigger_event="any" = "any front change"
    trigger_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=True,
    )
    # "start" | "stop" | "any"
    trigger_event: Mapped[str | None] = mapped_column(String(16), nullable=True)
    delay_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- repeated (structured schedule) ---
    # "daily" | "weekly" | "monthly"
    schedule_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # HH:MM in schedule_tz
    schedule_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # bitmask, Mon=1, Tue=2, ..., Sun=64
    schedule_dow_mask: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_dom: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_tz: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- repeated (advanced cron) ---
    # When set, takes precedence over the structured fields above.
    cron_expression: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # --- scoping (repeated only) ---
    # "system" | "member"
    scope: Mapped[str] = mapped_column(
        String(8), nullable=False, default="system", server_default="system"
    )
    digest_when_absent: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default="true"
    )

    # Runtime state
    last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    scope_members: Mapped[list["Member"]] = relationship(
        secondary=reminder_scope_members,
    )
    pending: Mapped[list["ReminderPending"]] = relationship(
        back_populates="reminder",
        cascade="all, delete-orphan",
    )


class ReminderPending(UUIDMixin, Base):
    """A reminder that *should* have fired at `scheduled_for` but didn't
    because no scope-member was fronting. Drained as part of a digest
    notification when any scope-member next starts fronting."""

    __tablename__ = "reminder_pending"

    reminder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reminders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
    )

    reminder: Mapped[Reminder] = relationship(back_populates="pending")

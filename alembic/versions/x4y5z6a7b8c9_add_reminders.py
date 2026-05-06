"""Add reminder, reminder_scope_member, reminder_pending tables

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-05-06

Two kinds of reminders share the same row:

- automated: triggered by a front-change event (member starts/stops/any),
  fires `delay_hours` after the event lands. No queue.
- repeated: cron-style schedule (daily/weekly/monthly + time-of-day, with
  an "advanced" raw cron string for power users). Optionally member-scoped
  with a digest-on-next-front-of-scope-member fallback.

Both ride on top of an existing notification_channel for delivery.
Reminder pending rows queue up missed firings for member-scoped repeated
reminders; capped at 5 per reminder, oldest dropped on overflow.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "x4y5z6a7b8c9"
down_revision = "w3x4y5z6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "system_id",
            UUID(as_uuid=True),
            sa.ForeignKey("systems.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        # title + body are encrypted at rest (matching member descriptions
        # and journal entries) — they're free-text user content.
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        # 'automated' (front-event-triggered) or 'repeated' (cron-scheduled)
        sa.Column("trigger_type", sa.String(16), nullable=False),
        # --- automated trigger fields ---
        # null trigger_member_id with trigger_event=any = "any front change"
        sa.Column(
            "trigger_member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("trigger_event", sa.String(16), nullable=True),
        sa.Column("delay_seconds", sa.Integer(), nullable=True),
        # --- repeated schedule fields (structured) ---
        sa.Column("schedule_kind", sa.String(16), nullable=True),
        sa.Column("schedule_time", sa.String(5), nullable=True),  # HH:MM
        # bitmask, Mon=1, Tue=2, ..., Sun=64
        sa.Column("schedule_dow_mask", sa.Integer(), nullable=True),
        sa.Column("schedule_dom", sa.Integer(), nullable=True),
        sa.Column("schedule_tz", sa.String(64), nullable=True),
        # --- repeated schedule fields (advanced cron) ---
        # When set, takes precedence over the structured fields above.
        sa.Column("cron_expression", sa.String(120), nullable=True),
        # --- scoping (repeated only) ---
        sa.Column(
            "scope",
            sa.String(8),
            nullable=False,
            server_default="system",
        ),
        sa.Column(
            "digest_when_absent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        # --- runtime state ---
        # Tracks the last fire so the scheduler can detect missed firings
        # across server downtime without firing every missed slot.
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "reminder_scope_members",
        sa.Column(
            "reminder_id",
            UUID(as_uuid=True),
            sa.ForeignKey("reminders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "reminder_pending",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "reminder_id",
            UUID(as_uuid=True),
            sa.ForeignKey("reminders.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "scheduled_for", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("reminder_pending")
    op.drop_table("reminder_scope_members")
    op.drop_table("reminders")

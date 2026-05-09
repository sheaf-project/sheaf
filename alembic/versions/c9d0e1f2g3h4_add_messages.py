"""Add board messages

Revision ID: c9d0e1f2g3h4
Revises: b8c9d0e1f2g3
Create Date: 2026-05-08

Two-board message surface (system-wide global board + per-member
walls). Messages share the polymorphic content_revisions table for
edit history (`target_type='message'`). Adds three things:

  - `messages` table: the messages themselves, with parent_message_id
    for single-level reply chains and a soft-delete column.
  - `message_read_state` table: per-member last-seen markers driving
    the on-front-start prompt and the sidebar unread badge.
  - Three columns on `members` for the per-member opt-in toggles
    governing the on-front-start prompt: notify_on_front_global,
    notify_on_front_self, notify_on_front_member_ids.
  - `safety_applies_to_messages` column on `systems` for the new
    System Safety category.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "c9d0e1f2g3h4"
down_revision = "b8c9d0e1f2g3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "system_id",
            UUID(as_uuid=True),
            sa.ForeignKey("systems.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("board_kind", sa.String(8), nullable=False),
        sa.Column(
            "board_member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "author_member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "parent_message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "deleted_at", sa.DateTime(timezone=True), nullable=True
        ),
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
    op.create_index(
        "ix_messages_board_created",
        "messages",
        ["system_id", "board_kind", "board_member_id", "created_at"],
    )

    op.create_table(
        "message_read_state",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("board_kind", sa.String(8), nullable=False),
        sa.Column(
            "board_member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_message_read_state_lookup",
        "message_read_state",
        ["member_id", "board_kind", "board_member_id"],
        unique=True,
    )

    # Per-member front-start notify opts.
    op.add_column(
        "members",
        sa.Column(
            "notify_on_front_global",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "members",
        sa.Column(
            "notify_on_front_self",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "members",
        sa.Column(
            "notify_on_front_member_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # New System Safety category.
    op.add_column(
        "systems",
        sa.Column(
            "safety_applies_to_messages",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("systems", "safety_applies_to_messages")
    op.drop_column("members", "notify_on_front_member_ids")
    op.drop_column("members", "notify_on_front_self")
    op.drop_column("members", "notify_on_front_global")
    op.drop_index(
        "ix_message_read_state_lookup", table_name="message_read_state"
    )
    op.drop_table("message_read_state")
    op.drop_index("ix_messages_board_created", table_name="messages")
    op.drop_table("messages")

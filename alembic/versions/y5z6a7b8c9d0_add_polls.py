"""Add polls, poll_options, poll_votes, poll_vote_events

Revision ID: y5z6a7b8c9d0
Revises: x4y5z6a7b8c9
Create Date: 2026-05-07

Polls let a system run small in-house decisions with a short audit
trail. Headmates vote "as" a member who must be in the current front
at vote time; every cast/change/withdraw appends an event row with a
fronting snapshot. Question, description, and option text are
encrypted at rest. Polls have a creation-time deadline that cannot be
moved; cleanup runs `retention_days` after closes_at.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

revision = "y5z6a7b8c9d0"
down_revision = "x4y5z6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "systems",
        sa.Column(
            "safety_applies_to_polls",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "polls",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "system_id",
            UUID(as_uuid=True),
            sa.ForeignKey("systems.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("results_visibility", sa.String(16), nullable=False),
        sa.Column(
            "closes_at", sa.DateTime(timezone=True), nullable=False, index=True
        ),
        sa.Column("retention_days", sa.Integer(), nullable=False),
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
        "poll_options",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "poll_id",
            UUID(as_uuid=True),
            sa.ForeignKey("polls.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("poll_id", "position", name="uq_poll_option_position"),
    )

    op.create_table(
        "poll_votes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "poll_id",
            UUID(as_uuid=True),
            sa.ForeignKey("polls.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "voted_as_member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "option_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
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
        sa.UniqueConstraint(
            "poll_id", "voted_as_member_id", name="uq_poll_vote_per_member"
        ),
    )

    op.create_table(
        "poll_vote_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "poll_id",
            UUID(as_uuid=True),
            sa.ForeignKey("polls.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "voted_as_member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column(
            "option_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column(
            "fronting_member_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_poll_vote_events_poll_created",
        "poll_vote_events",
        ["poll_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_poll_vote_events_poll_created", table_name="poll_vote_events")
    op.drop_table("poll_vote_events")
    op.drop_table("poll_votes")
    op.drop_table("poll_options")
    op.drop_table("polls")
    op.drop_column("systems", "safety_applies_to_polls")

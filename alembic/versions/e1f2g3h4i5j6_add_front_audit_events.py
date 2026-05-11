"""Add front_audit_events

Revision ID: e1f2g3h4i5j6
Revises: d0e1f2g3h4i5
Create Date: 2026-05-10

Per-front-entry audit log. Append-only; one row per explicit edit. ON
DELETE CASCADE on front_id binds the log lifetime to the front entry
itself — purging a front (retention, manual delete) also purges its
audit history.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "e1f2g3h4i5j6"
down_revision = "d0e1f2g3h4i5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "front_audit_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "front_id",
            UUID(as_uuid=True),
            sa.ForeignKey("fronts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "fronting_member_ids",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("before_snapshot", JSONB, nullable=False),
        sa.Column("after_snapshot", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_front_audit_events_front_id",
        "front_audit_events",
        ["front_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_front_audit_events_front_id", table_name="front_audit_events"
    )
    op.drop_table("front_audit_events")

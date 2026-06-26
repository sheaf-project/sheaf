"""Add activity_events (account activity log)

Revision ID: r9s0t1u2v3w4
Revises: q8r9s0t1u2v3
Create Date: 2026-06-26

A user-facing, Safety-independent log of consequential and automated
account/system actions (password/email/2FA/API-key/session changes, data
export requests, account deletion, import completed, export ready) so
nothing happens silently. Append-only; aged out by cleanup_activity_events.

A plain CREATE TABLE (+ its two enum types and indexes); no lock on
existing tables, so no lock_timeout dance needed.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "r9s0t1u2v3w4"
down_revision: Union[str, None] = "q8r9s0t1u2v3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTOR = sa.Enum("user", "system", name="activity_actor_type")
_ACTION = sa.Enum(
    "password_changed",
    "email_change_requested",
    "email_changed",
    "totp_enabled",
    "totp_disabled",
    "recovery_codes_regenerated",
    "api_key_created",
    "api_key_revoked",
    "session_revoked",
    "trusted_device_revoked",
    "account_deletion_scheduled",
    "account_deletion_cancelled",
    "data_export_requested",
    "import_completed",
    "export_ready",
    name="activity_action",
)


def upgrade() -> None:
    op.create_table(
        "activity_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", _ACTOR, nullable=False),
        sa.Column("action", _ACTION, nullable=False),
        sa.Column("target_label", sa.String(length=200), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_activity_events_created_at", "activity_events", ["created_at"]
    )
    op.create_index(
        "ix_activity_events_user_created",
        "activity_events",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_activity_events_user_created", table_name="activity_events")
    op.drop_index("ix_activity_events_created_at", table_name="activity_events")
    op.drop_table("activity_events")
    _ACTION.drop(op.get_bind(), checkfirst=True)
    _ACTOR.drop(op.get_bind(), checkfirst=True)

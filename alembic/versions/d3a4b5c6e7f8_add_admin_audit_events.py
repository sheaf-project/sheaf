"""Add admin_audit_events table

Revision ID: d3a4b5c6e7f8
Revises: c2f3a4b5d6e7
Create Date: 2026-06-04

Append-only log of state-changing admin actions (user_update, approve,
reject, member-limit, safety-reset, pending-bypass) plus the few
privacy-sensitive admin reads worth recording (import-log views).
Routine list / get reads are deliberately not logged — the table is
mutation-rich, not browse-noisy.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d3a4b5c6e7f8"
down_revision: Union[str, None] = "c2f3a4b5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ACTION_VALUES = [
    "user_update",
    "user_approve",
    "user_reject",
    "user_member_limit_set",
    "user_safety_reset",
    "user_pending_bypass",
    "import_log_view",
]

_TARGET_TYPE_VALUES = [
    "user",
    "system",
    "pending_action",
    "import_job",
]


def upgrade() -> None:
    action_enum = postgresql.ENUM(
        *_ACTION_VALUES, name="admin_audit_action", create_type=True
    )
    target_enum = postgresql.ENUM(
        *_TARGET_TYPE_VALUES, name="admin_audit_target_type", create_type=True
    )
    action_enum.create(op.get_bind(), checkfirst=True)
    target_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "admin_audit_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "admin_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "action",
            postgresql.ENUM(
                *_ACTION_VALUES, name="admin_audit_action", create_type=False
            ),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_type",
            postgresql.ENUM(
                *_TARGET_TYPE_VALUES,
                name="admin_audit_target_type",
                create_type=False,
            ),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "target_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("before_json", postgresql.JSONB(), nullable=True),
        sa.Column("after_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
        sa.Column("admin_email", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("admin_audit_events")
    postgresql.ENUM(name="admin_audit_action").drop(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(name="admin_audit_target_type").drop(
        op.get_bind(), checkfirst=True
    )

"""Add System Safety columns, pending_actions, and safety_change_requests

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "m3n4o5p6q7r8"
down_revision = "l2m3n4o5p6q7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent guard: the predecessor of this migration shipped on a
    # pre-rebase feature branch under a different revision id, so some
    # dev DBs already have these columns/tables. Skip anything already there.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_systems_cols = {c["name"] for c in inspector.get_columns("systems")}
    existing_tables = set(inspector.get_table_names())

    if "safety_grace_period_days" not in existing_systems_cols:
        op.add_column(
            "systems",
            sa.Column(
                "safety_grace_period_days",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    for category in ("members", "groups", "tags", "fields", "fronts"):
        col = f"safety_applies_to_{category}"
        if col not in existing_systems_cols:
            op.add_column(
                "systems",
                sa.Column(
                    col,
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )

    if "pending_actions" not in existing_tables:
        _create_pending_actions()
    if "safety_change_requests" not in existing_tables:
        _create_safety_change_requests()


def _create_pending_actions() -> None:
    op.create_table(
        "pending_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "system_id",
            UUID(as_uuid=True),
            sa.ForeignKey("systems.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_label", sa.String(200), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "requested_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("finalize_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fronting_member_ids", JSONB, nullable=False),
        sa.Column("fronting_member_names", JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancelled_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
    )
    op.create_index(
        "ix_pending_actions_due",
        "pending_actions",
        ["system_id", "status", "finalize_after"],
    )


def _create_safety_change_requests() -> None:
    op.create_table(
        "safety_change_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "system_id",
            UUID(as_uuid=True),
            sa.ForeignKey("systems.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "requested_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("finalize_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changes", JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_safety_change_requests_due",
        "safety_change_requests",
        ["system_id", "status", "finalize_after"],
    )


def downgrade() -> None:
    op.drop_index("ix_safety_change_requests_due", table_name="safety_change_requests")
    op.drop_table("safety_change_requests")
    op.drop_index("ix_pending_actions_due", table_name="pending_actions")
    op.drop_table("pending_actions")

    for category in ("fronts", "fields", "tags", "groups", "members"):
        op.drop_column("systems", f"safety_applies_to_{category}")
    op.drop_column("systems", "safety_grace_period_days")

"""Add export_jobs table for async data exports

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "t0u1v2w3x4y5"
down_revision = "s9t0u1v2w3x4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "export_jobs" in inspector.get_table_names():
        return
    op.create_table(
        "export_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "include_images",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_location", sa.String(500), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_export_jobs_user_requested",
        "export_jobs",
        ["user_id", "requested_at"],
    )
    op.create_index(
        "ix_export_jobs_pending",
        "export_jobs",
        ["requested_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_export_jobs_expires",
        "export_jobs",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_export_jobs_expires", table_name="export_jobs")
    op.drop_index("ix_export_jobs_pending", table_name="export_jobs")
    op.drop_index("ix_export_jobs_user_requested", table_name="export_jobs")
    op.drop_table("export_jobs")

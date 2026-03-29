"""Add deletion fields and job_runs table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # User deletion columns
    op.add_column(
        "users",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("deletion_reminders_sent", sa.String(50), nullable=True),
    )

    # Job runs table
    op.create_table(
        "job_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_name", sa.String(100), nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("items_processed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("details", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("job_runs")
    op.drop_column("users", "deletion_reminders_sent")
    op.drop_column("users", "deletion_requested_at")

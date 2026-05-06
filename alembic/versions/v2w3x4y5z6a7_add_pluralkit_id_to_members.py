"""Add pluralkit_id column on members

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa

revision = "v2w3x4y5z6a7"
down_revision = "u1v2w3x4y5z6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("members")}
    if "pluralkit_id" not in cols:
        op.add_column(
            "members",
            sa.Column("pluralkit_id", sa.String(length=8), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("members", "pluralkit_id")

"""Add coalesce_contiguous_fronts column on systems

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa

revision = "u1v2w3x4y5z6"
down_revision = "t0u1v2w3x4y5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("systems")}
    if "coalesce_contiguous_fronts" not in cols:
        op.add_column(
            "systems",
            sa.Column(
                "coalesce_contiguous_fronts",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade() -> None:
    op.drop_column("systems", "coalesce_contiguous_fronts")

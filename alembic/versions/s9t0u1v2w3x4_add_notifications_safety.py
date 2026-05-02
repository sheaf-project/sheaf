"""Add safety_applies_to_notifications column on systems

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-05-01
"""

from alembic import op
import sqlalchemy as sa

revision = "s9t0u1v2w3x4"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("systems")}
    if "safety_applies_to_notifications" not in cols:
        op.add_column(
            "systems",
            sa.Column(
                "safety_applies_to_notifications",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    op.drop_column("systems", "safety_applies_to_notifications")

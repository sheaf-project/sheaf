"""Add users.disable_cdn_during_ddos column

Revision ID: 9706550595b1
Revises: l8m9n0o1p2q3
Create Date: 2026-05-29

Per-user opt-out flag for cf-shield mass-invalidation. When the operator
engages cf-shield, users with this flag set have their sessions revoked
so their traffic does not unwittingly traverse the Cloudflare CDN. The
column is created unconditionally even on selfhost; instances that do
not run cf-shield (settings.shield_mode_enabled=false) simply never
read it.
"""

import sqlalchemy as sa

from alembic import op

revision = "9706550595b1"
down_revision = "l8m9n0o1p2q3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "disable_cdn_during_ddos",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "disable_cdn_during_ddos")

"""usage_daily_sketches: add auth_kind to the key

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-09-01

Splits the aggregate usage sketches by auth kind - "client" (session cookie or
JWT bearer) versus "api" (API key) - so interactive use and automation are
counted separately. auth_kind joins (day, scope) in the composite key.

The table is aggregate OPS data that the flush job repopulates from Redis every
few minutes, and it holds nothing user-attributable, so this recreates it rather
than doing a fragile primary-key alter: any rows present are stale sketch bytes
under the old (day, scope) shape and are regenerated on the next flush. Still
excluded from the Article 20 user-data export.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("usage_daily_sketches")
    op.create_table(
        "usage_daily_sketches",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("auth_kind", sa.String(length=8), nullable=False),
        sa.Column("sketch", sa.LargeBinary(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("day", "scope", "auth_kind"),
    )


def downgrade() -> None:
    op.drop_table("usage_daily_sketches")
    op.create_table(
        "usage_daily_sketches",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("sketch", sa.LargeBinary(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("day", "scope"),
    )

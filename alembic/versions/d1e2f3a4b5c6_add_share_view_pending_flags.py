"""Add staged (grace-windowed) flag flips to share views

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-08-01

Turning include_bio / include_fronting / fronting_show_count ON while a view is
already shared exposes more, so it has to serve the System Safety grace window
like every other exposing change. The new value is staged in a pending_* column
and the finalize sweep copies it onto the live flag once flags_activate_at
passes. NULL means nothing is staged, which is what every existing row gets.

All four columns are nullable with no default, so Postgres adds them without a
table rewrite. lock_timeout is belt-and-braces per house style.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.add_column(
        "share_views",
        sa.Column("pending_include_bio", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "share_views",
        sa.Column("pending_include_fronting", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "share_views",
        sa.Column("pending_fronting_show_count", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "share_views",
        sa.Column("flags_activate_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.drop_column("share_views", "flags_activate_at")
    op.drop_column("share_views", "pending_fronting_show_count")
    op.drop_column("share_views", "pending_include_fronting")
    op.drop_column("share_views", "pending_include_bio")

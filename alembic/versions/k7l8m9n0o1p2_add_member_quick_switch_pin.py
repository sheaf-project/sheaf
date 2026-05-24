"""Add members.quick_switch_pin

Revision ID: k7l8m9n0o1p2
Revises: j6k7l8m9n0o1
Create Date: 2026-05-23

Nullable integer pin priority for the quick-switch / top-fronters list.
NULL means unpinned; a value pins the member ahead of the recency-ranked
results, ordered ascending. No index — member sets per system are small
and the ranking happens in Python.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "k7l8m9n0o1p2"
down_revision = "j6k7l8m9n0o1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "members",
        sa.Column("quick_switch_pin", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("members", "quick_switch_pin")

"""Add front_retention_days to systems

Revision ID: x5y6z7a8b9c0
Revises: w4x5y6z7a8b9
Create Date: 2026-07-05

User-opt-in front-history privacy retention window, in days. 0 = off = keep
fronting history forever (the default); a positive value is the age-out window,
keyed off each front's real-world end time (ended_at) by the sweep, which lands
in a later change. This migration only adds the setting column - nothing deletes
yet.

ADD COLUMN with a constant non-null server_default ('0') is metadata-only on
modern Postgres: existing rows adopt the default without a table rewrite. It
still briefly takes ACCESS EXCLUSIVE, so fail fast rather than queue behind a
long-running session.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x5y6z7a8b9c0"
down_revision: Union[str, None] = "w4x5y6z7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "systems",
        sa.Column(
            "front_retention_days",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("systems", "front_retention_days")

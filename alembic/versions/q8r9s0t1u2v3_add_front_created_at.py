"""Add fronts.created_at

Revision ID: q8r9s0t1u2v3
Revises: p7q8r9s0t1u2
Create Date: 2026-06-24

The fronts table never had a row-creation timestamp, only the front's
real-world started_at / ended_at. That gap is what let the retention job
key off started_at and delete just-imported historical fronts. This adds
created_at as the correct "when did this row land here" timestamp.

ADD COLUMN with a non-volatile server_default (now() is stable within the
statement) is metadata-only on modern Postgres: existing rows get the
migration-time value without a table rewrite, which is exactly the safe
backfill we want (imported/old history is treated as created now, never
retroactively eligible for anything). It still briefly takes ACCESS
EXCLUSIVE, so fail fast rather than queue behind a long-running session.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q8r9s0t1u2v3"
down_revision: Union[str, None] = "p7q8r9s0t1u2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "fronts",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("fronts", "created_at")

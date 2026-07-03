"""Add content_revisions.inserted_at

Revision ID: v3w4x5y6z7a8
Revises: u2v3w4x5y6z7
Create Date: 2026-07-02

content_revisions.created_at is overwritten on import from the *source* edit
timestamp, so an imported-old edit history sorts as the "oldest" revisions and
gets trimmed first even though it only just landed. This adds inserted_at as
the true "when did this row arrive here" timestamp, which the retention trim
now orders by. Same class of fix as fronts.created_at (see
q8r9s0t1u2v3_add_front_created_at).

ADD COLUMN with a non-volatile server_default (now() is stable within the
statement) is metadata-only on modern Postgres: existing rows get the
migration-time value without a table rewrite, which is exactly the safe
backfill we want (imported/old history is treated as inserted now, never
retroactively counted as old). It still briefly takes ACCESS EXCLUSIVE, so
fail fast rather than queue behind a long-running session.

The parallel index (retention orders by inserted_at) is created plainly inside
the same migration. content_revisions is far less hot than fronts, so a plain
CREATE INDEX is acceptable here rather than the CONCURRENTLY / autocommit_block
dance.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v3w4x5y6z7a8"
down_revision: Union[str, None] = "u2v3w4x5y6z7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "content_revisions",
        sa.Column(
            "inserted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_content_revisions_inserted",
        "content_revisions",
        ["inserted_at"],
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_index(
        "ix_content_revisions_inserted",
        table_name="content_revisions",
    )
    op.drop_column("content_revisions", "inserted_at")

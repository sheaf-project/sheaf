"""Add ix_fronts_system_ended_created for the free-tier retention sweep

Revision ID: s0t1u2v3w4x5
Revises: r9s0t1u2v3w4
Create Date: 2026-07-01

The free-tier retention sweep filters:

    system_id IN (...) AND created_at < cutoff
      AND ended_at IS NOT NULL AND ended_at < cutoff

(see sheaf/services/front_retention.py). Neither existing index covers
`created_at` - ix_fronts_system_current is (system_id, ended_at) and
ix_fronts_system_started is (system_id, started_at) - so the predicate
seq-scanned the fronts table. This composite (system_id, ended_at,
created_at) lets the sweep index-scan the closed, long-dormant rows.

Built CONCURRENTLY: the prod fronts table may be large and a plain
CREATE INDEX takes a SHARE lock that blocks writes for the whole build.
CONCURRENTLY cannot run inside a transaction, so the create is wrapped in
an autocommit block.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "s0t1u2v3w4x5"
down_revision: Union[str, None] = "r9s0t1u2v3w4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_fronts_system_ended_created",
            "fronts",
            ["system_id", "ended_at", "created_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_fronts_system_ended_created",
            table_name="fronts",
            postgresql_concurrently=True,
            if_exists=True,
        )

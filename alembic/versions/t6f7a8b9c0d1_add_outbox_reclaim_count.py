"""Add notification_outbox.reclaim_count

Revision ID: t6f7a8b9c0d1
Revises: s5e6f7a8b9c0
Create Date: 2026-11-03

Integer with a constant default: no table rewrite, but ADD COLUMN still
takes ACCESS EXCLUSIVE, so fail fast rather than queue the dispatcher's
claim queries behind a wait.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t6f7a8b9c0d1"
down_revision: str | None = "s5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "notification_outbox",
        sa.Column(
            "reclaim_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("notification_outbox", "reclaim_count")

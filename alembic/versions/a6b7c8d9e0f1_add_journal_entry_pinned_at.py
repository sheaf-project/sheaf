"""Add journal_entries.pinned_at

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-09-15

Lets an entry be pinned so it sits above the chronological list. NULL means
not pinned. Unpinning is gated by the journals System Safety category.

Nullable ADD COLUMN with no default is metadata-only, but it still briefly
takes ACCESS EXCLUSIVE, so fail fast rather than queue behind a long-running
session.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "journal_entries",
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("journal_entries", "pinned_at")

"""Add openplural_archive column to systems

Revision ID: o6p7q8r9s0t1
Revises: n5o6p7q8r9s0
Create Date: 2026-06-20

Holds the OpenPlural import residual: foreign data Sheaf cannot model
(other apps' `extensions` namespaces, the chat/relationships modules,
front_events/front_comments, non-tag taxonomy), preserved on import and
re-merged into the next OpenPlural export. Stored encrypted + zlib
compressed, so a plain nullable Text column. NULL when nothing was
preserved.

ADD COLUMN with no default is metadata-only on modern Postgres, but it
still briefly takes ACCESS EXCLUSIVE, so fail fast rather than queue.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "o6p7q8r9s0t1"
down_revision: Union[str, None] = "n5o6p7q8r9s0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "systems",
        sa.Column("openplural_archive", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("systems", "openplural_archive")

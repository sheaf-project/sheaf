"""Add member archive: members.archived_at + systems.safety_applies_to_archive

Revision ID: p7q8r9s0t1u2
Revises: o6p7q8r9s0t1
Create Date: 2026-06-22

Archive is a soft-hide for members (hidden from lists / switcher /
pickers, still shown in history) and a new optional System Safety
category that gates whether archiving requires re-auth. Both are plain
nullable / defaulted column adds.

ADD COLUMN with no default (archived_at) or a constant server_default
(safety_applies_to_archive) is metadata-only on modern Postgres, but it
still briefly takes ACCESS EXCLUSIVE, so fail fast rather than queue.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p7q8r9s0t1u2"
down_revision: Union[str, None] = "o6p7q8r9s0t1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "members",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "systems",
        sa.Column(
            "safety_applies_to_archive",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("systems", "safety_applies_to_archive")
    op.drop_column("members", "archived_at")

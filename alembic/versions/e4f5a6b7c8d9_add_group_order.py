"""Add groups.order

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-09-06

Groups had no user-controlled ordering: the list endpoint sorted by name and
that was the only order there was. This adds the same integer sort column
custom_field_definitions already carries, so the owner can arrange groups
themselves; the API sorts by (order, name), so rows still at the default keep
their alphabetical placing.

ADD COLUMN with a constant server_default is metadata-only on modern Postgres
(existing rows read the default without a table rewrite). It still briefly
takes ACCESS EXCLUSIVE, so fail fast rather than queue behind a long-running
session.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "groups",
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("groups", "order")

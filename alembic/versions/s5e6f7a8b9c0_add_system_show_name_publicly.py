"""Add systems.show_name_publicly

Revision ID: s5e6f7a8b9c0
Revises: r4c5d6e7f8a9
Create Date: 2026-10-24

A boolean with a constant default: Postgres 11+ records the default in the
catalogue rather than rewriting the table, so this is quick, but ADD COLUMN
still takes ACCESS EXCLUSIVE. Fail fast if anything holds the table rather
than queue every systems query behind the wait.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s5e6f7a8b9c0"
down_revision: str | None = "r4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "systems",
        sa.Column(
            "show_name_publicly",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("systems", "show_name_publicly")

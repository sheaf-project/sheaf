"""Add the slider custom field type

Revision ID: r4c5d6e7f8a9
Revises: q3b4c5d6e7f8
Create Date: 2026-10-16

``fieldtype`` is a native Postgres enum, so the new value is added with
ALTER TYPE ... ADD VALUE, which cannot run inside a transaction block, hence
the autocommit block. Purely additive: no existing row changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "r4c5d6e7f8a9"
down_revision: str | None = "q3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE fieldtype ADD VALUE IF NOT EXISTS 'slider'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for an enum; removing a value means recreating
    # the type and rewriting every dependent column. Downgrade is a no-op, the
    # repo convention for enum-value adds.
    pass

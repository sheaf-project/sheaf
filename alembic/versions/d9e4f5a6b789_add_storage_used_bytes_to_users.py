"""Add storage_used_bytes to users

Revision ID: d9e4f5a6b789
Revises: c8d3e4f5a678
Create Date: 2026-03-20 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d9e4f5a6b789"
down_revision: Union[str, None] = "c8d3e4f5a678"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "storage_used_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "storage_used_bytes")

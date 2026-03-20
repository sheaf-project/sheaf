"""Add member_limit to users

Revision ID: e0f5a6b7c890
Revises: d9e4f5a6b789
Create Date: 2026-03-20 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e0f5a6b7c890"
down_revision: Union[str, None] = "d9e4f5a6b789"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("member_limit", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "member_limit")

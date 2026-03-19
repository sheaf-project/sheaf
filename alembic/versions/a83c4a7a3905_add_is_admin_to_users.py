"""Add is_admin and recovery_codes to users

Revision ID: a83c4a7a3905
Revises: c6d42e8a1ef5
Create Date: 2026-03-18 19:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a83c4a7a3905"
down_revision: Union[str, None] = "c6d42e8a1ef5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("recovery_codes", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "recovery_codes")
    op.drop_column("users", "is_admin")

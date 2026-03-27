"""Add signup_ip to users

Revision ID: b2c3d4e5f6a7
Revises: 105395daaa47
Create Date: 2026-03-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = '105395daaa47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('signup_ip', sa.String(45), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'signup_ip')

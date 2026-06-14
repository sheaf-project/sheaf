"""Add banner_url to members

Revision ID: k0a1b2c3d4e5
Revises: k1d2c3b4a5e6
Create Date: 2026-06-13

Wide header image for member profiles. Same storage/trust model as
avatar_url (bare storage key or external URL); nullable, no default.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k0a1b2c3d4e5"
down_revision: Union[str, None] = "k1d2c3b4a5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "members",
        sa.Column("banner_url", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("members", "banner_url")

"""Add banner_url to systems

Revision ID: b2a3n4n5e6r7
Revises: a2b3c4d5e6f7
Create Date: 2026-09-25

Wide header image for the system profile, the system-level twin of
Member.banner_url. Same storage/trust model as avatar_url (bare storage
key or external URL); nullable, no default.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2a3n4n5e6r7"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "systems",
        sa.Column("banner_url", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("systems", "banner_url")

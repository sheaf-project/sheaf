"""add_replace_fronts_default_to_systems

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d567
Create Date: 2026-03-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f1a2b3c4d567"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "systems",
        sa.Column(
            "replace_fronts_default",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )


def downgrade() -> None:
    op.drop_column("systems", "replace_fronts_default")

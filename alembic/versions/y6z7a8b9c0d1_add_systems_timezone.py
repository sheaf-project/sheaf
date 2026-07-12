"""Add timezone to systems

Revision ID: y6z7a8b9c0d1
Revises: x5y6z7a8b9c0
Create Date: 2026-07-12

Global display-timezone preference. NULL = "auto" = each device renders in its
own local clock (the default); a non-null value is an IANA zone name validated
at the API/import boundary. This migration only adds the nullable column -
rendering against it lands in the client changes.

ADD COLUMN of a nullable column with no default is metadata-only on modern
Postgres: no table rewrite, existing rows read as NULL. It still briefly takes
ACCESS EXCLUSIVE, so fail fast rather than queue behind a long-running session.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y6z7a8b9c0d1"
down_revision: Union[str, None] = "x5y6z7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "systems",
        sa.Column("timezone", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("systems", "timezone")

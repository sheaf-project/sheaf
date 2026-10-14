"""Add platforms to server announcements

Revision ID: q3b4c5d6e7f8
Revises: p2a3s4s5k6e7
Create Date: 2026-10-13

An announcement can be aimed at particular clients: a list of platform names
(web, android, ios, watch). Empty, which every existing row becomes, means
the announcement is for everyone, which is exactly what every existing row
meant before the column existed.

ADD COLUMN with a constant server default is metadata-only on modern
Postgres (no table rewrite), but it still takes ACCESS EXCLUSIVE briefly, so
fail fast rather than queue behind a long-running session.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "q3b4c5d6e7f8"
down_revision: Union[str, None] = "p2a3s4s5k6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "server_announcements",
        sa.Column(
            "platforms",
            postgresql.ARRAY(sa.String(length=16)),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("server_announcements", "platforms")

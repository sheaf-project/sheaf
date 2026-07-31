"""add show_member_created_date to systems

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-07-29

Per-system display preference (opt-in, default off) controlling whether the
web UI shows each member's created date on their profile. Purely a display
toggle; the member's created_at is already exposed on the API.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9c0d1e2f3a4"
down_revision: str | None = "a8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "systems",
        sa.Column(
            "show_member_created_date",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("systems", "show_member_created_date")

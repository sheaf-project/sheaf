"""Add polls.include_custom_fronts column

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-05-07

A per-poll boolean for opting custom-front members (Asleep, Away, etc.)
in or out of voting. Default false: custom fronts are typically system
states, not voters. Added as a follow-up so dev databases that already
ran the polls migration before this column existed pick it up cleanly
on the next upgrade.
"""

import sqlalchemy as sa

from alembic import op

revision = "z6a7b8c9d0e1"
down_revision = "y5z6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "polls",
        sa.Column(
            "include_custom_fronts",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("polls", "include_custom_fronts")

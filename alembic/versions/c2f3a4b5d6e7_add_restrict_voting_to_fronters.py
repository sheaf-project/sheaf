"""Add polls.restrict_voting_to_fronters

Revision ID: c2f3a4b5d6e7
Revises: b1e2f3a4c5d6
Create Date: 2026-06-03

Per-poll opt-in for "voter must be in current front at vote time".
Previously the gate was hardcoded on. New default is False so polls
behave like journals (any member can author / vote regardless of front
state). The server_default 'false' covers existing rows; pre-existing
polls in v0.3.1 betas adopt the new permissive default.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c2f3a4b5d6e7"
down_revision: Union[str, None] = "b1e2f3a4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "polls",
        sa.Column(
            "restrict_voting_to_fronters",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("polls", "restrict_voting_to_fronters")

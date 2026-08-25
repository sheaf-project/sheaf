"""Add publishing_blocked to systems

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-25

Operator takedown latch on a system. Set by the admin revoke-all lever so a
taken-down profile cannot be republished by the owner POSTing a fresh grant
seconds later; cleared only by an admin, with a reason, never by the owner.
Defaults false so every existing row backfills to "not blocked" - the normal
state for every account that has never been actioned.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "systems",
        sa.Column(
            "publishing_blocked",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("systems", "publishing_blocked")

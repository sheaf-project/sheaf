"""Add the share-grant revoke-all value to the admin audit action enum

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-08-17

The admin takedown lever for the public-profile surface (revoke every share
grant on one system) writes an audit row like every other admin action, so its
action value has to exist in the enum first.

In its own migration because ALTER TYPE ... ADD VALUE cannot run inside a
transaction block.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS "
        "'system_share_grants_revoke_all'"
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE; downgrade is a no-op.
    pass

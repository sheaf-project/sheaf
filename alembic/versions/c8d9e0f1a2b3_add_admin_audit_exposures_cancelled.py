"""Add the cancel-staged-exposures value to the admin audit action enum

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-09-18

Support can now call off every staged flip-to-public raise on a system, the
un-exposing counterpart to the pending-action bypass. It writes an audit row
like every other admin action, so its action value has to exist in the enum
first.

In its own migration because ALTER TYPE ... ADD VALUE cannot run inside a
transaction block.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS "
        "'user_exposures_cancelled'"
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE; downgrade is a no-op.
    pass

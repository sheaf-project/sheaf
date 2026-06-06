"""Add user_ban / user_unban to admin_audit_action

Revision ID: h7e8a9b0c1d2
Revises: g6d7e8a9b0c1
Create Date: 2026-06-06

Permanent ban variant of the soft-suspend flow. The BANNED account
status already exists in the `accountstatus` enum; this just wires
the audit-action values.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "h7e8a9b0c1d2"
down_revision: Union[str, None] = "g6d7e8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS 'user_ban'"
    )
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS 'user_unban'"
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE; downgrade is a no-op.
    pass

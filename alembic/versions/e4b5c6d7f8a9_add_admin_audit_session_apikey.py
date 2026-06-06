"""Add session-revoke and api-keys-rotate-all to admin_audit_action

Revision ID: e4b5c6d7f8a9
Revises: d3a4b5c6e7f8
Create Date: 2026-06-05

Two new enum values for the PR 3 small-actions batch:

  - user_session_revoke: admin terminated a single user session.
  - user_api_keys_rotate_all: admin force-revoked every API key on the
    target account.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e4b5c6d7f8a9"
down_revision: Union[str, None] = "d3a4b5c6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS 'user_session_revoke'"
    )
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS 'user_api_keys_rotate_all'"
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE; the safe downgrade is a no-op. Rows
    # already inserted with these values would block enum rebuild, so
    # we leave the type widened forever — adding the values back on a
    # re-upgrade is idempotent.
    pass

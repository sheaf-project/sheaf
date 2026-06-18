"""Add security-view actions to admin_audit_action

Revision ID: m4n5o6p7q8r9
Revises: l3m4n5o6p7q8
Create Date: 2026-06-17

Two new enum values for auditing privacy-sensitive reads of the new
security-event log:

  - security_ip_lookup: admin searched activity by IP / subnet.
  - security_history_view: admin viewed one account's security events.

Kept in its own migration because ALTER TYPE ... ADD VALUE cannot run
inside a transaction block alongside the table DDL.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "m4n5o6p7q8r9"
down_revision: Union[str, None] = "l3m4n5o6p7q8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS 'security_ip_lookup'"
    )
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS 'security_history_view'"
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE; leave the type widened (idempotent on
    # re-upgrade), matching the other enum-extension migrations.
    pass

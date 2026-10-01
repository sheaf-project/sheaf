"""Add the passkey values to the activity_action and security_event_type enums

Revision ID: p2a3s4s5k6e7
Revises: n0t1i2f3y4d5
Create Date: 2026-09-30

`ActivityAction.PASSKEY_ADDED` / `PASSKEY_REMOVED` (the account activity
log) and `SecurityEventType.PASSKEY_ENROLL` (the security event log) are
members of native Postgres enums, so each needs ALTER TYPE ... ADD VALUE,
which cannot run inside a transaction block; hence the autocommit block.
tests/test_native_enum_migrations.py fails if a member is added without
its value here.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "p2a3s4s5k6e7"
down_revision: Union[str, None] = "n0t1i2f3y4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE activity_action ADD VALUE IF NOT EXISTS 'passkey_added'"
        )
        op.execute(
            "ALTER TYPE activity_action ADD VALUE IF NOT EXISTS 'passkey_removed'"
        )
        op.execute(
            "ALTER TYPE security_event_type ADD VALUE IF NOT EXISTS 'passkey_enroll'"
        )


def downgrade() -> None:
    # Additive enum values; Postgres has no DROP VALUE. No-op, per convention.
    pass

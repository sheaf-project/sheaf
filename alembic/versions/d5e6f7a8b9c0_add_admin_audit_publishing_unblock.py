"""Add the publishing-unblock value to the admin audit action enum

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-08-25

The admin lever that clears a system's publishing_blocked latch writes an audit
row like every other admin action, so its action value has to exist in the enum
first. Sibling to the revoke-all takedown value it undoes.

In its own migration because ALTER TYPE ... ADD VALUE cannot run inside a
transaction block, and separate from the publishing_blocked column add for the
same reason (that one is an ordinary transactional DDL).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS "
        "'system_publishing_unblock'"
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE; downgrade is a no-op.
    pass

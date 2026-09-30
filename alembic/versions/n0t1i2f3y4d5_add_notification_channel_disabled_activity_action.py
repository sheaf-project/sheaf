"""Add notification_channel_disabled to the activity_action enum

Revision ID: n0t1i2f3y4d5
Revises: p1a2s3s4k5e6
Create Date: 2026-09-30

`ActivityAction.NOTIFICATION_CHANNEL_DISABLED` was added to the Python enum
in 1.6.0 without this migration. `activity_action` is a native Postgres
enum, so every activity row written with the value failed at commit, and
because the dispatcher wrote it in the same transaction as the channel
switch-off and the outbox row's terminal state, the whole feature ("a
channel failing for a day is switched off and its owner told") silently
never happened: the channel stayed active, the row stayed pending, and the
next lease re-claimed it forever. Adding the value lets the next dispatcher
tick finish what it has been trying to do since; the frozen rows go
terminal on their own.

ALTER TYPE ... ADD VALUE cannot run inside a transaction block, hence the
autocommit block, exactly as t1u2v3w4x5y6 did for `retention_pruned`.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "n0t1i2f3y4d5"
down_revision: Union[str, None] = "p1a2s3s4k5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE activity_action ADD VALUE IF NOT EXISTS "
            "'notification_channel_disabled'"
        )


def downgrade() -> None:
    # Postgres has no DROP VALUE for an enum; removing a value means recreating
    # the type and rewriting every dependent column. Additive value, no-op
    # downgrade, matching the repo convention for enum-value adds.
    pass

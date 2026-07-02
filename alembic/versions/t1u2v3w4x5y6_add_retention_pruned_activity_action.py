"""Add retention_pruned to the activity_action enum

Revision ID: t1u2v3w4x5y6
Revises: s0t1u2v3w4x5
Create Date: 2026-07-01

The free-tier front-retention and revision-retention sweeps already emit a
content-free per-user account-activity trace when they remove rows, guarded
by `getattr(ActivityAction, "RETENTION_PRUNED", None)` (see
sheaf/services/front_retention.py and sheaf/services/retention.py). Those
call sites are dormant until this enum value exists; this migration lights
them up. `activity_action` is a native Postgres enum, so the value is added
with ALTER TYPE ... ADD VALUE, which cannot run inside a transaction block -
hence the autocommit block.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "t1u2v3w4x5y6"
down_revision: Union[str, None] = "s0t1u2v3w4x5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE activity_action ADD VALUE IF NOT EXISTS 'retention_pruned'"
        )


def downgrade() -> None:
    # Postgres has no DROP VALUE for an enum; removing a value means recreating
    # the type and rewriting every dependent column, which is not worth it for
    # an additive value. Downgrade is a no-op (matches the repo convention for
    # enum-value adds, e.g. h7e8a9b0c1d2_add_admin_audit_ban_unban).
    pass

"""Add suspend / unsuspend / dossier-export to admin_audit_action

Revision ID: g6d7e8a9b0c1
Revises: f5c6d7e8a9b0
Create Date: 2026-06-05

Three new enum values for the PR 4 batch:

  - user_suspend: admin soft-banned an account (with or without an
    expiry timestamp).
  - user_unsuspend: admin lifted a soft-ban early, OR the background
    sweep lifted it at expiry. The `admin_user_id` column is NULL
    when the sweep is the actor.
  - user_dossier_export: admin pulled the GDPR Article 15 metadata
    bundle for the account. Privacy-sensitive read, reason required
    on the endpoint.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "g6d7e8a9b0c1"
down_revision: Union[str, None] = "f5c6d7e8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS 'user_suspend'"
    )
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS 'user_unsuspend'"
    )
    op.execute(
        "ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS 'user_dossier_export'"
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE; downgrade is a no-op. Idempotent
    # IF NOT EXISTS on the upgrade path means re-running is safe.
    pass

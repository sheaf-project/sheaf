"""Add recovery-cluster / invite / job values to the admin audit enums

Revision ID: i8f9b0c1d2e3
Revises: h7e8a9b0c1d2
Create Date: 2026-06-09

The account-recovery endpoints (reset-password, change-email,
disable-totp, verify-email, cancel-deletion), invite management, and
manual job triggers now write audit rows; these are their action
values plus two new target types (invite, job).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "i8f9b0c1d2e3"
down_revision: Union[str, None] = "h7e8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIONS = (
    "user_password_reset",
    "user_email_change",
    "user_totp_disable",
    "user_email_verify",
    "user_deletion_cancel",
    "invite_create",
    "invite_delete",
    "job_trigger",
)

_TARGET_TYPES = ("invite", "job")


def upgrade() -> None:
    op.execute("COMMIT")
    for value in _ACTIONS:
        op.execute(
            f"ALTER TYPE admin_audit_action ADD VALUE IF NOT EXISTS '{value}'"
        )
    for value in _TARGET_TYPES:
        op.execute(
            f"ALTER TYPE admin_audit_target_type ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade() -> None:
    # Postgres has no DROP VALUE; downgrade is a no-op.
    pass

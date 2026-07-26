"""Add step-up gate values to the security_event_type enum

Revision ID: a8b9c0d1e2f3
Revises: z7a8b9c0d1e2
Create Date: 2026-07-25

The sensitive re-auth (step-up) gates - the admin dashboard, the full
account-data export, and the email-change / TOTP-enroll / TOTP-disable /
recovery-code-regen / account-deletion actions - previously only advanced the
lockout counter on a failed re-auth, so a security responder had no durable
trail of attempts against them. They now call record_security_event with these
new event types (both on failure and on success). `security_event_type` is a
native Postgres enum, so each value is added with ALTER TYPE ... ADD VALUE,
which cannot run inside a transaction block - hence the autocommit block. The
call sites tolerate a missing value (record_security_event is best-effort and
never raises), so this is a safe additive migration.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "z7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_VALUES = (
    "admin_step_up",
    "account_data_access",
    "email_change",
    "totp_enroll",
    "totp_disable",
    "recovery_codes_regen",
    "account_deletion",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in _NEW_VALUES:
            op.execute(
                f"ALTER TYPE security_event_type ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    # Postgres has no DROP VALUE for an enum; removing a value means recreating
    # the type and rewriting every dependent column, which is not worth it for
    # additive values. Downgrade is a no-op (matches the repo convention for
    # enum-value adds, e.g. t1u2v3w4x5y6_add_retention_pruned_activity_action).
    pass

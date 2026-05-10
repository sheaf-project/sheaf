"""Add push_device_tokens for mobile push

Revision ID: d0e1f2g3h4i5
Revises: c9d0e1f2g3h4
Create Date: 2026-05-09

Mobile push (FCM + APNs) is account-scoped: one row per
(account, platform, token), looked up at delivery time via the
channel's redeemed_by_account_id. The channel itself stores no
transport credential.

Existing destination_type values that referred to mobile push were
gated behind _RESERVED_TYPES, so no rows in production carry the
older `apns` placeholder; this migration is purely additive.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "d0e1f2g3h4i5"
down_revision = "c9d0e1f2g3h4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_device_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("install_id", sa.String(64), nullable=True),
        sa.Column("app_version", sa.String(32), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "account_id",
            "platform",
            "token",
            name="uq_push_device_account_platform_token",
        ),
    )
    op.create_index(
        "ix_push_device_tokens_account_id",
        "push_device_tokens",
        ["account_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_push_device_tokens_account_id", table_name="push_device_tokens"
    )
    op.drop_table("push_device_tokens")

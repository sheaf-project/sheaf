"""Add front-change notification tables

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-05-01
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "r8s9t0u1v2w3"
down_revision = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watch_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "system_id",
            UUID(as_uuid=True),
            sa.ForeignKey("systems.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(120), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_watch_tokens_system_active",
        "watch_tokens",
        ["system_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "notification_channels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "watch_token_id",
            UUID(as_uuid=True),
            sa.ForeignKey("watch_tokens.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("destination_type", sa.String(16), nullable=False),
        sa.Column(
            "destination_config",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "destination_state",
            sa.String(24),
            nullable=False,
            server_default="pending_registration",
        ),
        sa.Column(
            "event_type", sa.String(32), nullable=False, server_default="front_change"
        ),
        sa.Column("activation_code_hash", sa.String, nullable=True),
        sa.Column(
            "activation_code_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "redeemed_by_account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recipient_management_token_hash", sa.String, nullable=True),
        sa.Column(
            "base_all_members",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "base_include_private",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "trigger_on_start", sa.Boolean, nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "trigger_on_stop", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "trigger_on_cofront_change",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "cofront_redaction", sa.String(8), nullable=False, server_default="count"
        ),
        sa.Column(
            "payload_sensitivity",
            sa.String(8),
            nullable=False,
            server_default="full",
        ),
        sa.Column(
            "debounce_seconds", sa.Integer, nullable=False, server_default="30"
        ),
        sa.Column(
            "aggregation_window_seconds",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column("quiet_hours", JSONB, nullable=True),
        sa.Column("email_delivery_mode", sa.String(16), nullable=True),
        sa.Column("email_monthly_cap", sa.Integer, nullable=True),
        sa.Column(
            "email_monthly_used", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("email_month_anchor", sa.Date, nullable=True),
        sa.Column("webhook_secret_encrypted", sa.String, nullable=True),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_notification_channels_active",
        "notification_channels",
        ["destination_state"],
        postgresql_where=sa.text("destination_state = 'active'"),
    )

    op.create_table(
        "notification_channel_group_rules",
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rule", sa.String(8), nullable=False),
        sa.Column(
            "include_private",
            sa.String(8),
            nullable=False,
            server_default="inherit",
        ),
        sa.CheckConstraint(
            "rule IN ('include','exclude')", name="ck_group_rule_action"
        ),
        sa.CheckConstraint(
            "include_private IN ('inherit','yes','no')",
            name="ck_group_rule_include_private",
        ),
    )

    op.create_table(
        "notification_channel_member_rules",
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rule", sa.String(8), nullable=False),
        sa.CheckConstraint(
            "rule IN ('include','exclude')", name="ck_member_rule_action"
        ),
    )

    op.create_table(
        "notification_outbox",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("event_payload", JSONB, nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deliver_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(64), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "failed_attempts", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("next_retry_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_notification_outbox_due",
        "notification_outbox",
        ["deliver_after"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )
    op.create_index(
        "ix_notification_outbox_channel_due",
        "notification_outbox",
        ["channel_id", "deliver_after"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )

    # Reserved for the email destination type. Created now so the email
    # branch is purely additive when it lands.
    op.create_table(
        "email_verifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("confirm_token_hash", sa.String, nullable=False),
        sa.Column("block_token_hash", sa.String, nullable=False),
        sa.Column("confirm_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_action", sa.String(8), nullable=True),
    )

    op.create_table(
        "email_suppressions",
        sa.Column("address_hash", sa.String, primary_key=True),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("suppressed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("email_suppressions")
    op.drop_table("email_verifications")
    op.drop_index(
        "ix_notification_outbox_channel_due", table_name="notification_outbox"
    )
    op.drop_index("ix_notification_outbox_due", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_table("notification_channel_member_rules")
    op.drop_table("notification_channel_group_rules")
    op.drop_index(
        "ix_notification_channels_active", table_name="notification_channels"
    )
    op.drop_table("notification_channels")
    op.drop_index("ix_watch_tokens_system_active", table_name="watch_tokens")
    op.drop_table("watch_tokens")

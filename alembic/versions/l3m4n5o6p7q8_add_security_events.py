"""Add security_events log + IP/user-agent on admin_audit_events

Revision ID: l3m4n5o6p7q8
Revises: k0a1b2c3d4e5
Create Date: 2026-06-17

Append-only auth-funnel log (logins, registrations, password reset /
change) with the originating client IP, for credential-stuffing
detection and IP-based search. IP is INET (not String(45)) so subnet
queries work. Also backfills admin_audit_events with ip/user_agent so
admin actions can be checked for an unexpected origin.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "l3m4n5o6p7q8"
down_revision: Union[str, None] = "k0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EVENT_TYPE_VALUES = (
    "login",
    "register",
    "password_reset_request",
    "password_reset_complete",
    "password_change",
)


def upgrade() -> None:
    event_type = postgresql.ENUM(
        *_EVENT_TYPE_VALUES, name="security_event_type", create_type=True
    )
    event_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "security_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "event_type",
            postgresql.ENUM(
                *_EVENT_TYPE_VALUES,
                name="security_event_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
    )
    op.create_index(
        "ix_security_events_ip_created",
        "security_events",
        ["ip", "created_at"],
    )
    op.create_index(
        "ix_security_events_user_created",
        "security_events",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_security_events_type_created",
        "security_events",
        ["event_type", "created_at", "ip"],
    )

    op.add_column(
        "admin_audit_events",
        sa.Column("ip", postgresql.INET(), nullable=True),
    )
    op.add_column(
        "admin_audit_events",
        sa.Column("user_agent", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_audit_events", "user_agent")
    op.drop_column("admin_audit_events", "ip")
    op.drop_index("ix_security_events_type_created", table_name="security_events")
    op.drop_index("ix_security_events_user_created", table_name="security_events")
    op.drop_index("ix_security_events_ip_created", table_name="security_events")
    op.drop_table("security_events")
    postgresql.ENUM(name="security_event_type").drop(
        op.get_bind(), checkfirst=True
    )

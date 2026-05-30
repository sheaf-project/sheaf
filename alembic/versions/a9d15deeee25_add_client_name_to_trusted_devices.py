"""Add trusted_devices.client_name column

Revision ID: a9d15deeee25
Revises: 9706550595b1
Create Date: 2026-05-30

Friendly client identifier (e.g. "Sheaf Android", "Firefox") for
trusted-device rows, mirroring the same field on sessions. Populated
at mint time from X-Sheaf-Client when supplied, otherwise parsed from
user_agent. The Settings -> Account "Trusted devices" listing reads
this instead of guessing from user_agent each render, which lets the
mobile app identify itself properly without the listing falling back
to the okhttp default UA.

Server-default empty string so existing rows backfill cleanly; the
list endpoint re-parses user_agent for those.
"""

import sqlalchemy as sa

from alembic import op

revision = "a9d15deeee25"
down_revision = "9706550595b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trusted_devices",
        sa.Column(
            "client_name",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("trusted_devices", "client_name")

"""Add `enabled` and `label` to push_device_tokens

Revision ID: g3h4i5j6k7l8
Revises: f2g3h4i5j6k7
Create Date: 2026-05-13

`enabled` lets a recipient mute one of their devices without
unregistering it entirely (e.g. work phone over the weekend). The
dispatcher's mobile-push fan-out skips disabled rows.

`label` is the user-visible device name (e.g. "Sarah's iPhone"). The
mobile app sends it at registration; the receiving-tab device list
renders it. Nullable — old rows registered before this migration get
a platform-based default at render time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "g3h4i5j6k7l8"
down_revision = "f2g3h4i5j6k7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "push_device_tokens",
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "push_device_tokens",
        sa.Column("label", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("push_device_tokens", "label")
    op.drop_column("push_device_tokens", "enabled")

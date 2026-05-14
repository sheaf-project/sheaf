"""Add `paused_by_sender` to notification_channels

Revision ID: h4i5j6k7l8m9
Revises: g3h4i5j6k7l8
Create Date: 2026-05-13

The `disabled` channel state covered two distinct user actions:
the owner pausing the channel, and the recipient unsubscribing. From
the recipient's side both looked identical and the UI labelled them
both as "Unsubscribed" — confusing in the owner-paused case ("but I
didn't unsubscribe"). This column splits the two: True means the
owner paused, False means the recipient unsubscribed (or the column
was never set). Cleared on re-enable.

Default false matches "recipient unsubscribed" semantics on existing
disabled rows, which is the conservative interpretation — we can't
tell which side disabled a pre-migration row, and "Unsubscribed" is
the existing label so behaviour is unchanged for legacy rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h4i5j6k7l8m9"
down_revision = "g3h4i5j6k7l8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_channels",
        sa.Column(
            "paused_by_sender",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("notification_channels", "paused_by_sender")

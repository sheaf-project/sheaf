"""Collapse fcm/apns_dev/apns_prod into mobile_push

Revision ID: f2g3h4i5j6k7
Revises: e1f2g3h4i5j6
Create Date: 2026-05-13

The platform-specific destination types had to be chosen at channel
creation, even though the OS the recipient was on was unknown to the
owner. Unifying as `mobile_push` removes that mismatch: the channel
binds to a Sheaf account at redemption, and the dispatcher fans out
across every `push_device_tokens` row for that account, routing each
token to FCM (Android) or APNs (iOS, dev/prod per-token) automatically.

In-place column update — destination_type is a String column (not a
PG enum), so no type changes needed. Rows that were previously
fcm / apns_dev / apns_prod become mobile_push. No data loss: the
account-side fan-out covers every platform automatically. The legacy
enum values stay in the Python enum for read-back of historical
audit / export records, but channel creation now refuses them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2g3h4i5j6k7"
down_revision = "e1f2g3h4i5j6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE notification_channels SET destination_type = 'mobile_push' "
            "WHERE destination_type IN ('fcm', 'apns_dev', 'apns_prod')"
        )
    )
    # push_device_tokens.platform stays per-device (the platform is a
    # property of the registered device, not the channel) — no change
    # needed there.


def downgrade() -> None:
    # Best-effort: there's no original-platform information once a row
    # has been collapsed to mobile_push, so we can't restore the exact
    # prior value. The closest reasonable behaviour is to leave the
    # rows as mobile_push and let the application's legacy enum members
    # surface them on read. A hard downgrade would have to choose one
    # of the three legacy values arbitrarily, which is worse than just
    # accepting the lossy semantic.
    pass

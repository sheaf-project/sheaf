"""Add share_views link-preview mode columns (system and member) plus pending twins

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-09-17

Per-view choice of what a chat client shows when somebody pastes the view's URL:
the generic "a public system profile powered by Sheaf" card (`generic`, the
default) or the system's name, avatar and a description snippet
(`system_details`).

`String(16)` rather than a Postgres ENUM, matching `share_grants.subject_type`
and the status columns on this table and for the same stated reason: a third mode
then needs no type migration, only a new member of the Python enum.

`server_default='generic'` rather than nullable, so every view that existed
before this column reads as the quiet option with no backfill pass - a new
exposure setting must never arrive switched on for people who were not asked.

`member_link_preview_mode` is the same choice for a MEMBER PERMALINK's URL, and a
separate column rather than a third value of the first one, because the two
exposures do not contain one another: a system card reveals the system's name,
avatar and description snippet, a member card reveals one member's name and
avatar. An ordered dial would have forced system details on as the price of member
cards, which is a constraint invented by the column rather than by privacy.

Each gets the staging twin every other exposure flag on this table carries:
raising a mode while the view is already shared is a loosening, so with a grace
window configured the new value parks in the pending column and the share finalize
sweep promotes it. NULL means nothing staged.

Four ADD COLUMNs. On PG11+ a non-volatile default is metadata-only (no table
rewrite), but each still takes ACCESS EXCLUSIVE briefly, and while waiting for it
they queue every other query on share_views behind them - so fail fast rather
than drag the table's whole queue behind a forgotten idle session, and re-run in a
quiet window. All four are in ONE migration so that a single lock acquisition
covers them rather than four.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "share_views",
        sa.Column(
            "link_preview_mode",
            sa.String(length=16),
            nullable=False,
            server_default="generic",
        ),
    )
    op.add_column(
        "share_views",
        sa.Column(
            "member_link_preview_mode",
            sa.String(length=16),
            nullable=False,
            server_default="generic",
        ),
    )
    op.add_column(
        "share_views",
        sa.Column("pending_link_preview_mode", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "share_views",
        sa.Column(
            "pending_member_link_preview_mode", sa.String(length=16), nullable=True
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("share_views", "pending_member_link_preview_mode")
    op.drop_column("share_views", "pending_link_preview_mode")
    op.drop_column("share_views", "member_link_preview_mode")
    op.drop_column("share_views", "link_preview_mode")

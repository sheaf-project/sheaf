"""Group privacy ceiling + per-view display flags

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-08-11

Two groups of columns, all additive:

- ``groups.privacy`` / ``pending_privacy`` / ``privacy_activates_at`` - a group
  now carries its own exposure ceiling, the same three-level vocabulary a
  member and a member edge already speak. It reuses the existing ``privacylevel``
  Postgres type rather than inventing a parallel one: "how private is this?" is
  the same question whoever is asking. Raising a group to ``public`` while a
  view would actually serve it is a loosening, so it stages in the pending pair
  and the sharing finalizer promotes it once the window has elapsed.
- ``share_views.include_members`` / ``include_groups`` (+ their pending twins)
  and ``share_views.member_permalinks`` - what a view puts on the page.
  ``include_members`` defaults TRUE so every view that exists today keeps
  serving exactly the roster it serves now; the other two default false,
  because a new capability must never arrive switched on.
  ``member_permalinks`` gets no pending twin on purpose: it exposes no new
  data, only a stable address for members the roster already shows, so there
  is no exposure to wait out.

``create_type=False`` on the enum for the usual reason: ``privacylevel`` was
created by the initial schema migration and must not be re-created here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c6d7e8f9a0b1"
down_revision: str | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _privacy_enum() -> postgresql.ENUM:
    # Same type members.privacy and systems.privacy already use. Value order
    # matches the initial schema's CREATE TYPE; it is not significant, but
    # keeping it identical avoids a spurious diff in autogenerate.
    return postgresql.ENUM(
        "public",
        "friends",
        "private",
        name="privacylevel",
        create_type=False,
    )


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.add_column(
        "groups",
        sa.Column(
            "privacy",
            _privacy_enum(),
            nullable=False,
            server_default="private",
        ),
    )
    op.add_column(
        "groups",
        sa.Column("pending_privacy", _privacy_enum(), nullable=True),
    )
    op.add_column(
        "groups",
        sa.Column(
            "privacy_activates_at", sa.DateTime(timezone=True), nullable=True
        ),
    )

    op.add_column(
        "share_views",
        sa.Column(
            "include_members",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    op.add_column(
        "share_views",
        sa.Column("pending_include_members", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "share_views",
        sa.Column(
            "include_groups",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "share_views",
        sa.Column("pending_include_groups", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "share_views",
        sa.Column(
            "member_permalinks",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.drop_column("share_views", "member_permalinks")
    op.drop_column("share_views", "pending_include_groups")
    op.drop_column("share_views", "include_groups")
    op.drop_column("share_views", "pending_include_members")
    op.drop_column("share_views", "include_members")
    op.drop_column("groups", "privacy_activates_at")
    op.drop_column("groups", "pending_privacy")
    op.drop_column("groups", "privacy")

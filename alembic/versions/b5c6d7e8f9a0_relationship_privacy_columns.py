"""Relationship privacy: type colour, staged edge raises, view flag

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-08-09

Three groups of columns, all additive:

- ``relationship_types.color`` - a per-type colour, same shape as the one
  members, groups and tags already carry.
- ``member_relationships.pending_visibility`` / ``visibility_activates_at`` -
  raising an edge to ``public`` while it would actually show is a loosening, so
  it stages here and the sharing finalizer promotes it once the grace window
  has elapsed. The live ``visibility`` column does not move meanwhile. Group
  edges get no staging pair: nothing projects them, so a group-edge raise
  exposes nothing to wait out.
- ``share_views.include_relationships`` / ``pending_include_relationships`` -
  the fourth per-view exposure flag, with the same staged-flip twin as the
  other three.

The enum values ``pending_visibility`` needs were added in a4b5c6d7e8f9, which
had to run in its own autocommit block; this migration only references the
existing type (``create_type=False``).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _visibility_enum() -> postgresql.ENUM:
    return postgresql.ENUM(
        "private",
        "friends",
        "public",
        name="relationshipvisibility",
        create_type=False,
    )


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.add_column(
        "relationship_types",
        sa.Column("color", sa.String(length=7), nullable=True),
    )
    op.add_column(
        "member_relationships",
        sa.Column("pending_visibility", _visibility_enum(), nullable=True),
    )
    op.add_column(
        "member_relationships",
        sa.Column(
            "visibility_activates_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "share_views",
        sa.Column(
            "include_relationships",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "share_views",
        sa.Column("pending_include_relationships", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.drop_column("share_views", "pending_include_relationships")
    op.drop_column("share_views", "include_relationships")
    op.drop_column("member_relationships", "visibility_activates_at")
    op.drop_column("member_relationships", "pending_visibility")
    op.drop_column("relationship_types", "color")

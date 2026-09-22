"""members: stage a privacy raise on the member itself

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-09-20

Gives Member the `pending_privacy` / `privacy_activates_at` pair that System,
Group and CustomFieldDefinition already carry, so a raise to public that would
actually expose the member waits out the System Safety grace window on the
member's own row and the share finalizer promotes it.

Until now a member raise flipped `privacy` at once and staged the exposure by
demoting the member's ShareViewMember rows to PENDING instead. That only works
while every roster is a curated list of rows to demote; a view that selects
members by their live ceiling has none, and a grace-period bypass in the
exposure surface is the one place that cannot have one. Staging the ceiling
itself closes that door for every kind of view at once.

Nothing to backfill: NULL means "nothing staged", which is true of every
member today. A raise mid-window at deploy time keeps waiting in its demoted
rows and is promoted by the same sweep as before; only raises made after this
lands use the columns.

``create_type=False`` on the enum for the usual reason: ``privacylevel`` was
created by the initial schema migration and must not be re-created here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _privacy_enum() -> postgresql.ENUM:
    # The same type members.privacy already uses. Value order matches the
    # initial schema's CREATE TYPE; not significant, but keeping it identical
    # avoids a spurious diff in autogenerate.
    return postgresql.ENUM(
        "public",
        "friends",
        "private",
        name="privacylevel",
        create_type=False,
    )


def upgrade() -> None:
    # Two nullable columns with no default: metadata-only in Postgres, but it
    # is still an ALTER TABLE on the busiest user-data table, so fail fast
    # rather than queue every member read behind a lock wait.
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "members",
        sa.Column("pending_privacy", _privacy_enum(), nullable=True),
    )
    op.add_column(
        "members",
        sa.Column(
            "privacy_activates_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("members", "privacy_activates_at")
    op.drop_column("members", "pending_privacy")

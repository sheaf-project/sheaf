"""Add the relationships System Safety category

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-08-27

Deleting a relationship type cascades every member and group edge drawn with
it, which is the widest blast radius of any single delete in the product, and
until now it had no grace window at all - only a client-side confirm. This adds
``systems.safety_applies_to_relationships`` so the type delete can queue a
``PendingAction`` like member, group and field deletes do.

Defaults FALSE, matching every other destructive category (only
``safety_applies_to_profile_visibility``, which gates an EXPOSING action, is on
by default). Nobody's delete behaviour changes until they arm it, and a
category that switched itself on would be a silent behaviour change on an
existing account's tooling.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lock-taking DDL: fail fast rather than queueing every other query on
    # `systems` behind a wait for the ACCESS EXCLUSIVE lock.
    op.execute("SET lock_timeout = '3s'")

    op.add_column(
        "systems",
        sa.Column(
            "safety_applies_to_relationships",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.drop_column("systems", "safety_applies_to_relationships")

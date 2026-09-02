"""Profile-visibility safety on by default + staged system-privacy raises

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-08-24

Two related changes, both additive:

- ``systems.safety_applies_to_profile_visibility`` now defaults TRUE, and every
  existing row is backfilled to TRUE. The category gates making things MORE
  visible, and the decided posture is that a public-facing raise should demand
  step-up out of the box. At the ``none`` auth tier that step-up verifies
  nothing, so the default costs nobody anything until they pick a tier; the
  grace window stays 0 by default, so "armed" means "re-auth first", not "wait a
  week". The unconditional backfill (rather than leaving pre-existing rows
  false) is correct here BECAUSE public profiles are unreleased: this lives on
  the preview line and the only systems that exist are on the test instance, so
  there is no owner who deliberately turned this category off to preserve. When
  it ships, accounts predating it should arrive with the safe default on, not
  the old inert false.

- ``systems.pending_privacy`` / ``systems.privacy_activates_at`` - the
  system-scope twin of the group/field/edge staged-raise columns. Raising the
  master switch (``systems.privacy``) to ``public`` while a grant would actually
  serve is a loosening; with a grace window the new level parks in
  ``pending_privacy`` and the share finalizer promotes it once
  ``privacy_activates_at`` passes, while ``privacy`` itself stays put. Lowering
  cancels any staged raise outright.

``create_type=False`` on the enum for the usual reason: ``privacylevel`` was
created by the initial schema migration and must not be re-created here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a0b1c2d3e4f5"
down_revision: str | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _privacy_enum() -> postgresql.ENUM:
    # Same type systems.privacy, members.privacy and groups.privacy already use.
    return postgresql.ENUM(
        "public",
        "friends",
        "private",
        name="privacylevel",
        create_type=False,
    )


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    # Flip the default on for new rows, then bring every existing row up to it.
    op.alter_column(
        "systems",
        "safety_applies_to_profile_visibility",
        server_default=sa.text("true"),
    )
    op.execute(
        "UPDATE systems SET safety_applies_to_profile_visibility = true"
    )

    op.add_column(
        "systems",
        sa.Column("pending_privacy", _privacy_enum(), nullable=True),
    )
    op.add_column(
        "systems",
        sa.Column(
            "privacy_activates_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.drop_column("systems", "privacy_activates_at")
    op.drop_column("systems", "pending_privacy")

    # Restore the original server-side default. Existing rows keep whatever
    # value they hold; there is no record of which were flipped by the backfill.
    op.alter_column(
        "systems",
        "safety_applies_to_profile_visibility",
        server_default=sa.text("false"),
    )

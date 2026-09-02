"""Custom-field definition privacy ceiling (staging pair + backfill)

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-08-12

``custom_field_definitions.privacy`` already exists: it has been on the model,
the API schemas and the export since the pre-views design. What it never had is
an enforcer. Nothing read it. A field appeared on a shared view purely because
the owner had selected it into that view, whatever level the column said.

This migration is where that stops being decorative, so it also has to deal
honestly with the rows it inherits:

- ``pending_privacy`` / ``privacy_activates_at`` are added, the same staging
  pair a group and a member edge carry, so a raise to ``public`` that would
  actually put a field in front of somebody can wait out the System Safety
  grace window instead of landing instantly.
- The DATA BACKFILL sets ``privacy = 'public'`` on every definition with an
  ACTIVE row in ``share_view_fields``. Those are the fields live profiles were
  serving a minute before this migration ran. Since the column was unenforced,
  a definition the owner deliberately selected into a view was being served
  regardless of the level stored next to it - most of them still say
  ``private``, the model default, because nothing ever made an owner set it.
  Enforcement starts here, and the backfill preserves what those profiles were
  actually publishing rather than silently blanking fields off live pages and
  leaving the owner to work out why. It deliberately does NOT touch a
  definition that is not actively selected anywhere: nothing was serving it, so
  its stored level (``private`` unless the owner said otherwise) stands, and no
  field is published by this migration that was not already published.
  PENDING rows are excluded on purpose - they are not being served yet, and the
  owner still has the whole grace window to change their mind.

``create_type=False`` on the enum for the usual reason: ``privacylevel`` was
created by the initial schema migration and must not be re-created here.

The downgrade drops the two new columns and leaves the backfilled ``privacy``
values exactly where they are. It cannot do otherwise honestly: nothing records
which rows the UPDATE above touched, so "undoing" it would mean guessing, and
every wrong guess in one direction hides a field the owner had published while
every wrong guess in the other publishes one they had not. Leaving the values
alone reproduces the pre-migration behaviour anyway - with the enforcement code
gone, ``privacy`` goes back to being a column nobody reads.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d7e8f9a0b1c2"
down_revision: str | None = "c6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _privacy_enum() -> postgresql.ENUM:
    # The same type custom_field_definitions.privacy already uses. Value order
    # matches the initial schema's CREATE TYPE; not significant, but keeping it
    # identical avoids a spurious diff in autogenerate.
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
        "custom_field_definitions",
        sa.Column("pending_privacy", _privacy_enum(), nullable=True),
    )
    op.add_column(
        "custom_field_definitions",
        sa.Column(
            "privacy_activates_at", sa.DateTime(timezone=True), nullable=True
        ),
    )

    # Preserve what live profiles were serving the moment before enforcement
    # arrived. See the module docstring for why this is the honest direction.
    op.execute(
        """
        UPDATE custom_field_definitions
        SET privacy = 'public'
        WHERE id IN (
            SELECT field_id FROM share_view_fields WHERE status = 'active'
        )
        """
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    # Deliberately no counter-UPDATE: a downgrade cannot know which rows the
    # backfill changed. See the module docstring.
    op.drop_column("custom_field_definitions", "privacy_activates_at")
    op.drop_column("custom_field_definitions", "pending_privacy")

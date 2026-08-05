"""Stage fronting-private guard releases

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-08-04

Clearing ``members.fronting_private`` can expose live presence through an
already-published view, so it needs the same grace-window lifecycle as other
profile-visibility loosenings. ``fronting_private_activates_at`` keeps the
guard live until the sharing finalizer promotes the change.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.add_column(
        "members",
        sa.Column(
            "fronting_private_activates_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.drop_column("members", "fronting_private_activates_at")

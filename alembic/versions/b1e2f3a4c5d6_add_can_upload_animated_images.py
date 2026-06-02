"""Add can_upload_animated_images to users

Revision ID: b1e2f3a4c5d6
Revises: a9d15deeee25
Create Date: 2026-06-02

Per-user override for animated avatar uploads (GIF / animated WebP).
Combined with the settings.allow_animated_uploads master switch and the
tier-based eligibility set in sheaf.files.animation_allowed. Defaults
False so existing rows backfill to no animation access.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b1e2f3a4c5d6"
down_revision: Union[str, None] = "a9d15deeee25"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "can_upload_animated_images",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "can_upload_animated_images")

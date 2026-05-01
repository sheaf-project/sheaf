"""Revision pinning: pinned_at on content_revisions + system toggles + cap

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-04-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q7r8s9t0u1v2"
down_revision: Union[str, None] = "p6q7r8s9t0u1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "content_revisions",
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_content_revisions_pinned",
        "content_revisions",
        ["target_type", "target_id", "pinned_at"],
        postgresql_where=sa.text("pinned_at IS NOT NULL"),
    )

    op.add_column(
        "systems",
        sa.Column(
            "safety_applies_to_revisions",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "systems",
        sa.Column(
            "auto_pin_first_revision",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "systems",
        sa.Column(
            "pinned_revision_max_per_target",
            sa.Integer(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("systems", "pinned_revision_max_per_target")
    op.drop_column("systems", "auto_pin_first_revision")
    op.drop_column("systems", "safety_applies_to_revisions")
    op.drop_index("ix_content_revisions_pinned", table_name="content_revisions")
    op.drop_column("content_revisions", "pinned_at")

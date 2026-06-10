"""Add failed_attempts to export_jobs

Revision ID: j9a0b1c2d3e4
Revises: i8f9b0c1d2e3
Create Date: 2026-06-09

Companion to the stale-RUNNING export recovery sweep: counts how many
times a job had to be reset after a crashed build so a poisoned export
parks as FAILED instead of crash-looping the worker forever.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j9a0b1c2d3e4"
down_revision: Union[str, None] = "i8f9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "export_jobs",
        sa.Column(
            "failed_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("export_jobs", "failed_attempts")

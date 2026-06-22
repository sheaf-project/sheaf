"""Add format column to export_jobs

Revision ID: n5o6p7q8r9s0
Revises: m4n5o6p7q8r9
Create Date: 2026-06-19

Async exports can now be built in two shapes: the native Sheaf zip
(export.json + images/) or an OpenPlural v0.1 bundle (openplural.json +
assets/). The chosen format is persisted on the job row so the build
worker knows which artefact to assemble.

ADD COLUMN with a constant server_default is a metadata-only change on
modern Postgres (no table rewrite), but it still briefly takes
ACCESS EXCLUSIVE, so fail fast rather than queue behind a long lock.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n5o6p7q8r9s0"
down_revision: Union[str, None] = "m4n5o6p7q8r9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "export_jobs",
        sa.Column(
            "format",
            sa.String(length=32),
            nullable=False,
            server_default="sheaf_native",
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("export_jobs", "format")

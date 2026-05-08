"""Add members.note and systems.note columns

Revision ID: b8c9d0e1f2g3
Revises: a7b8c9d0e1f2
Create Date: 2026-05-07

Lightweight scratchpad note per scope. One per member, one per system.
Encrypted at rest like description. Deliberately no revisions, no
System Safety integration, no sub-records — overwrite is the only
edit path. Soft-capped at ~5kb plaintext at the schema layer.
"""

import sqlalchemy as sa

from alembic import op

revision = "b8c9d0e1f2g3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("note", sa.Text(), nullable=True))
    op.add_column("systems", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("systems", "note")
    op.drop_column("members", "note")

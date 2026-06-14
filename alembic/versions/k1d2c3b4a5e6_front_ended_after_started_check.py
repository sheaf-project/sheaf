"""Add fronts ended-after-started CHECK constraint

Revision ID: k1d2c3b4a5e6
Revises: j9a0b1c2d3e4
Create Date: 2026-06-14

A closed front can't end before it starts. The front edit endpoint already
rejects this, but the create and import paths build Front rows directly and
bypassed the check, so a mis-ordered front could be persisted and then become
un-editable (every edit re-runs the guard on the bad pair). This adds the
constraint as the mechanical backstop across all write paths.

Added NOT VALID so the constraint is enforced for all new inserts/updates
without retroactively validating (and potentially failing on) any pre-existing
rows. A later migration can VALIDATE it once existing data is confirmed clean.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "k1d2c3b4a5e6"
down_revision: Union[str, None] = "j9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE fronts ADD CONSTRAINT ck_fronts_ended_after_started "
        "CHECK (ended_at IS NULL OR ended_at >= started_at) NOT VALID"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE fronts DROP CONSTRAINT ck_fronts_ended_after_started"
    )

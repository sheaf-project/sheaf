"""Add suspended_until and suspended_reason to users

Revision ID: f5c6d7e8a9b0
Revises: e4b5c6d7f8a9
Create Date: 2026-06-05

PR 4: soft-ban support. The `suspended` value already exists in the
`accountstatus` enum (added in 105395daaa47), so this migration only
adds the two new columns that scope the suspension:

  - suspended_until: NULL when status != SUSPENDED OR when the
    suspension is indefinite. The hourly unsuspend sweep + the auth
    dep both treat past-expiry suspends as effectively ACTIVE.
  - suspended_reason: Free-form operator note captured at suspend
    time, surfaced to the affected user at next login attempt.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f5c6d7e8a9b0"
down_revision: Union[str, None] = "e4b5c6d7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("suspended_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("suspended_reason", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "suspended_reason")
    op.drop_column("users", "suspended_until")

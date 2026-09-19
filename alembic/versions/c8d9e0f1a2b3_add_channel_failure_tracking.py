"""Track consecutive delivery failures per notification channel

Three additive columns on `notification_channels`:

- `disabled_reason` - why the server switched a channel off, when the cause was
  neither the owner pausing it nor the recipient unsubscribing. NULL for both
  of those and for every channel that is not disabled, which is correct for
  every existing row, so there is nothing to backfill.
- `consecutive_failures` / `failing_since` - the current unbroken run of
  delivery failures, counted on the channel rather than on one outbox message.
  Both default to "not currently failing", which is the right reading of every
  row that exists today: a channel mid-outage at deploy time simply starts its
  run at the next failure, an hour later than it might have. That is a better
  error than disabling something on the strength of history this migration
  cannot see.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lock-taking DDL: three ALTER TABLEs on a table the dispatcher writes to
    # on every delivery. Fail fast rather than queueing every notification in
    # the system behind a lock wait.
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "notification_channels",
        sa.Column("disabled_reason", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "notification_channels",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "notification_channels",
        sa.Column(
            "failing_since", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("notification_channels", "failing_since")
    op.drop_column("notification_channels", "consecutive_failures")
    op.drop_column("notification_channels", "disabled_reason")

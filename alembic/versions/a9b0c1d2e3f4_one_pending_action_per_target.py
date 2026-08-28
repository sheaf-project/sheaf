"""One open pending action per (system, action_type, target)

Revision ID: a9b0c1d2e3f4
Revises: f7a8b9c0d1e2
Create Date: 2026-08-27

`queue_pending_action` wrote a row unconditionally, so pressing Delete twice
on a safeguarded item queued it twice: two rows on the Safety page for one
thing, and cancelling either left the target still on its way out. The service
now checks before inserting; this index is the backstop for two requests
racing that check, and lets the service answer the loser with the same 409
instead of a 500.

Partial on `status = 'pending'` so cancelled / completed / errored rows pile up
as history and a target can be queued again after a cancel.

Existing duplicates have to go first or the index cannot be built. For each
key we keep the EARLIEST still-pending row and mark the rest cancelled (with
`cancelled_at`, no `cancelled_by_user_id` - nobody pressed anything). Keeping
the earliest keeps the grace window the owner actually started, so nothing
gains time it was not already going to get, and cancelling rather than
deleting leaves the extra rows visible as history rather than vanishing
silently. The kept row still finalizes on its original schedule, so no queued
action is lost by this - only its duplicates.

Built CONCURRENTLY (autocommit block) because pending_actions is written on
every safeguarded delete and a plain CREATE INDEX takes a lock that blocks
those writes for the whole build. CONCURRENTLY can leave an INVALID index
behind if it fails; the drop in `downgrade` clears one for a re-run.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lock-taking DML on a small table: fail fast rather than queueing every
    # other query on pending_actions behind a wait.
    op.execute("SET lock_timeout = '3s'")

    # Dedupe first - the index build below fails on any surviving duplicate.
    op.execute(
        """
        UPDATE pending_actions AS pa
           SET status = 'cancelled',
               cancelled_at = now()
         WHERE pa.status = 'pending'
           AND pa.id <> (
               SELECT keep.id
                 FROM pending_actions AS keep
                WHERE keep.status = 'pending'
                  AND keep.system_id = pa.system_id
                  AND keep.action_type = pa.action_type
                  AND keep.target_id = pa.target_id
                ORDER BY keep.requested_at ASC, keep.id ASC
                LIMIT 1
           )
        """
    )

    with op.get_context().autocommit_block():
        op.create_index(
            "uq_pending_actions_pending_target",
            "pending_actions",
            ["system_id", "action_type", "target_id"],
            unique=True,
            postgresql_concurrently=True,
            postgresql_where=sa.text("status = 'pending'"),
            if_not_exists=True,
        )


def downgrade() -> None:
    # The dedupe is not reversible: the cancelled duplicates stay cancelled.
    # They were redundant copies of a still-queued action, so re-queueing them
    # would only put the double rows back on the Safety page.
    with op.get_context().autocommit_block():
        op.drop_index(
            "uq_pending_actions_pending_target",
            table_name="pending_actions",
            postgresql_concurrently=True,
            if_exists=True,
        )

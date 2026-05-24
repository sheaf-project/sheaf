"""Dedupe message_read_state and make its unique index NULLS NOT DISTINCT

Revision ID: l8m9n0o1p2q3
Revises: k7l8m9n0o1p2
Create Date: 2026-05-24

The system board stores board_member_id = NULL. Postgres treats NULLs as
distinct in a unique index, so the get-or-create read-state race could
insert duplicate system-board rows (and the per-member board raced into
unique-violation 500s). Collapse the existing duplicates, keeping the
most-recently-seen row per (member, board_kind, board_member_id), then
recreate the unique index with NULLS NOT DISTINCT (PG15+) so the system
board is covered too and INSERT ... ON CONFLICT can dedupe it.
"""

from __future__ import annotations

from alembic import op

revision = "l8m9n0o1p2q3"
down_revision = "k7l8m9n0o1p2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the row with the latest last_seen_at per logical key (NULLs
    # grouped via IS NOT DISTINCT FROM); id breaks ties deterministically.
    op.execute(
        """
        DELETE FROM message_read_state a
        USING message_read_state b
        WHERE a.member_id = b.member_id
          AND a.board_kind = b.board_kind
          AND a.board_member_id IS NOT DISTINCT FROM b.board_member_id
          AND (
                a.last_seen_at < b.last_seen_at
                OR (a.last_seen_at = b.last_seen_at AND a.id < b.id)
              )
        """
    )
    op.drop_index("ix_message_read_state_lookup", table_name="message_read_state")
    op.execute(
        "CREATE UNIQUE INDEX ix_message_read_state_lookup "
        "ON message_read_state (member_id, board_kind, board_member_id) "
        "NULLS NOT DISTINCT"
    )


def downgrade() -> None:
    op.drop_index("ix_message_read_state_lookup", table_name="message_read_state")
    op.create_index(
        "ix_message_read_state_lookup",
        "message_read_state",
        ["member_id", "board_kind", "board_member_id"],
        unique=True,
    )

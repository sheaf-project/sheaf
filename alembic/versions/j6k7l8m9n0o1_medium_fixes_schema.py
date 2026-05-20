"""Schema hardening: indexes, a uniqueness constraint, server defaults

Revision ID: j6k7l8m9n0o1
Revises: i5j6k7l8m9n0
Create Date: 2026-05-18

A batch of small schema corrections:

- custom_field_values gains a UNIQUE(field_id, member_id) constraint
  (deduplicating any existing rows first) plus an index on member_id.
- uploaded_files.user_id is indexed — the cleanup job and per-user
  listings were full-scanning.
- The composite-PK association tables get an index on their second
  column so member-direction lookups don't heap-scan.
- The redundant single-column index on journal_entries.system_id is
  dropped; the (system_id, created_at) composite already covers it.
- pending_actions / safety_change_requests / client_settings get
  server defaults on their JSONB / status columns so raw-SQL inserts
  don't fail.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "j6k7l8m9n0o1"
down_revision = "i5j6k7l8m9n0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- custom_field_values: dedupe, then enforce one value per pair ----
    # Keep one arbitrary survivor per (field_id, member_id); duplicates
    # here are a bug, so which one survives doesn't matter.
    op.execute(
        """
        DELETE FROM custom_field_values
        WHERE ctid NOT IN (
            SELECT MAX(ctid) FROM custom_field_values
            GROUP BY field_id, member_id
        )
        """
    )
    op.create_unique_constraint(
        "uq_custom_field_values_field_member",
        "custom_field_values",
        ["field_id", "member_id"],
    )
    op.create_index(
        "ix_custom_field_values_member_id",
        "custom_field_values",
        ["member_id"],
    )

    # --- uploaded_files.user_id index -----------------------------------
    op.create_index("ix_uploaded_files_user_id", "uploaded_files", ["user_id"])

    # --- association tables: reverse-direction (member_id) indexes ------
    for table in (
        "front_members",
        "group_members",
        "member_tags",
        "reminder_scope_members",
    ):
        op.create_index(f"ix_{table}_member_id", table, ["member_id"])

    # --- drop the redundant journal_entries.system_id index -------------
    op.drop_index("ix_journal_entries_system_id", table_name="journal_entries")

    # --- server defaults so raw-SQL inserts don't fail ------------------
    op.alter_column(
        "pending_actions",
        "fronting_member_ids",
        server_default=sa.text("'[]'::jsonb"),
    )
    op.alter_column(
        "pending_actions",
        "fronting_member_names",
        server_default=sa.text("'[]'::jsonb"),
    )
    op.alter_column(
        "pending_actions", "status", server_default=sa.text("'pending'")
    )
    op.alter_column(
        "safety_change_requests", "status", server_default=sa.text("'pending'")
    )
    op.alter_column(
        "client_settings", "settings", server_default=sa.text("'{}'::jsonb")
    )


def downgrade() -> None:
    op.alter_column("client_settings", "settings", server_default=None)
    op.alter_column("safety_change_requests", "status", server_default=None)
    op.alter_column("pending_actions", "status", server_default=None)
    op.alter_column("pending_actions", "fronting_member_names", server_default=None)
    op.alter_column("pending_actions", "fronting_member_ids", server_default=None)

    op.create_index(
        "ix_journal_entries_system_id", "journal_entries", ["system_id"]
    )

    for table in (
        "front_members",
        "group_members",
        "member_tags",
        "reminder_scope_members",
    ):
        op.drop_index(f"ix_{table}_member_id", table_name=table)

    op.drop_index("ix_uploaded_files_user_id", table_name="uploaded_files")

    op.drop_index(
        "ix_custom_field_values_member_id", table_name="custom_field_values"
    )
    op.drop_constraint(
        "uq_custom_field_values_field_member",
        "custom_field_values",
        type_="unique",
    )

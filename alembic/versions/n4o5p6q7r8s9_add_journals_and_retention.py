"""Add journals, content revisions, retention trim notices, and System
extensions for journal/image safety + retention caps

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "n4o5p6q7r8s9"
down_revision = "m3n4o5p6q7r8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_systems_cols = {c["name"] for c in inspector.get_columns("systems")}
    existing_tables = set(inspector.get_table_names())

    for category in ("journals", "images"):
        col = f"safety_applies_to_{category}"
        if col not in existing_systems_cols:
            op.add_column(
                "systems",
                sa.Column(
                    col,
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )

    if "journal_max_revisions" not in existing_systems_cols:
        op.add_column(
            "systems",
            sa.Column("journal_max_revisions", sa.Integer(), nullable=True),
        )
    if "journal_max_revision_days" not in existing_systems_cols:
        op.add_column(
            "systems",
            sa.Column("journal_max_revision_days", sa.Integer(), nullable=True),
        )

    if "journal_entries" not in existing_tables:
        _create_journal_entries()
    if "content_revisions" not in existing_tables:
        _create_content_revisions()
    if "retention_trim_notices" not in existing_tables:
        _create_retention_trim_notices()


def _create_journal_entries() -> None:
    op.create_table(
        "journal_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "system_id",
            UUID(as_uuid=True),
            sa.ForeignKey("systems.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "visibility",
            sa.String(16),
            nullable=False,
            server_default="system",
        ),
        sa.Column(
            "author_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "author_member_ids",
            JSONB,
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "author_member_names",
            JSONB,
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "image_keys", JSONB, nullable=False, server_default="[]"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_journal_entries_system_id",
        "journal_entries",
        ["system_id"],
    )
    op.create_index(
        "ix_journal_entries_member_id",
        "journal_entries",
        ["member_id"],
    )
    op.create_index(
        "ix_journal_entries_system_created",
        "journal_entries",
        ["system_id", "created_at"],
    )
    op.create_index(
        "ix_journal_entries_system_member_created",
        "journal_entries",
        ["system_id", "member_id", "created_at"],
    )


def _create_content_revisions() -> None:
    op.create_table(
        "content_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "editor_member_ids",
            JSONB,
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "editor_member_names",
            JSONB,
            nullable=False,
            server_default="[]",
        ),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "image_keys", JSONB, nullable=False, server_default="[]"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_content_revisions_target",
        "content_revisions",
        ["target_type", "target_id", "created_at"],
    )
    op.create_index(
        "ix_content_revisions_created",
        "content_revisions",
        ["created_at"],
    )
    op.create_index(
        "ix_content_revisions_user",
        "content_revisions",
        ["user_id"],
    )


def _create_retention_trim_notices() -> None:
    op.create_table(
        "retention_trim_notices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_tier", sa.String(32), nullable=False),
        sa.Column("to_tier", sa.String(32), nullable=False),
        sa.Column(
            "reason",
            sa.String(64),
            nullable=False,
            server_default="tier_downgrade",
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_retention_trim_notices_user_id",
        "retention_trim_notices",
        ["user_id"],
    )
    op.create_index(
        "ix_retention_trim_notices_due",
        "retention_trim_notices",
        ["status", "effective_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retention_trim_notices_due",
        table_name="retention_trim_notices",
    )
    op.drop_index(
        "ix_retention_trim_notices_user_id",
        table_name="retention_trim_notices",
    )
    op.drop_table("retention_trim_notices")

    op.drop_index("ix_content_revisions_user", table_name="content_revisions")
    op.drop_index("ix_content_revisions_created", table_name="content_revisions")
    op.drop_index("ix_content_revisions_target", table_name="content_revisions")
    op.drop_table("content_revisions")

    op.drop_index(
        "ix_journal_entries_system_member_created",
        table_name="journal_entries",
    )
    op.drop_index(
        "ix_journal_entries_system_created",
        table_name="journal_entries",
    )
    op.drop_index(
        "ix_journal_entries_member_id",
        table_name="journal_entries",
    )
    op.drop_index(
        "ix_journal_entries_system_id",
        table_name="journal_entries",
    )
    op.drop_table("journal_entries")

    op.drop_column("systems", "journal_max_revision_days")
    op.drop_column("systems", "journal_max_revisions")
    op.drop_column("systems", "safety_applies_to_images")
    op.drop_column("systems", "safety_applies_to_journals")

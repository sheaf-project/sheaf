"""Add share views + grants, member share guards, adult attestation

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-07-21

The exposure model behind public profiles. A ShareView is an explicit allowlist
of members and custom fields; a ShareGrant points a subject (public, or an
opaque link token) at a view. Nothing is publicly readable unless it is both
inside a view AND that view has an active grant.

Statuses and subject_type are String(16) rather than Postgres ENUMs so that
adding the parked `user` subject type (friends) later needs no type migration.

`uq_share_grants_one_public` is a PARTIAL unique index: at most one non-revoked
public grant per system, since "public" is a single audience. Revoked rows are
excluded so a system can re-publish after going dark.

New columns are all nullable or constant-defaulted booleans, so Postgres adds
them without a table rewrite. lock_timeout is belt-and-braces per house style.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.create_table(
        "share_views",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "system_id",
            UUID(as_uuid=True),
            sa.ForeignKey("systems.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "include_bio", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "include_fronting", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "fronting_show_count",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("system_id", "name", name="uq_share_views_system_name"),
    )

    op.create_table(
        "share_view_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "view_id",
            UUID(as_uuid=True),
            sa.ForeignKey("share_views.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="active"
        ),
        sa.Column("activates_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("view_id", "member_id", name="uq_share_view_members"),
    )
    op.create_index(
        "ix_share_view_members_due", "share_view_members", ["status", "activates_at"]
    )

    op.create_table(
        "share_view_groups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "view_id",
            UUID(as_uuid=True),
            sa.ForeignKey("share_views.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("view_id", "group_id", name="uq_share_view_groups"),
    )

    op.create_table(
        "share_view_fields",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "view_id",
            UUID(as_uuid=True),
            sa.ForeignKey("share_views.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "field_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_field_definitions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="active"
        ),
        sa.Column("activates_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("view_id", "field_id", name="uq_share_view_fields"),
    )
    op.create_index(
        "ix_share_view_fields_due", "share_view_fields", ["status", "activates_at"]
    )

    op.create_table(
        "share_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "system_id",
            UUID(as_uuid=True),
            sa.ForeignKey("systems.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "view_id",
            UUID(as_uuid=True),
            sa.ForeignKey("share_views.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("subject_type", sa.String(16), nullable=False),
        # Keyed HMAC of the link token, never the token itself.
        sa.Column("token_hash", sa.String(64), nullable=True, unique=True, index=True),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="pending"
        ),
        sa.Column("activates_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_share_grants_one_public",
        "share_grants",
        ["system_id"],
        unique=True,
        postgresql_where=sa.text("subject_type = 'public' AND revoked_at IS NULL"),
    )
    op.create_index("ix_share_grants_due", "share_grants", ["status", "activates_at"])

    # Hard per-member guards, enforced in the projection query itself.
    op.add_column(
        "members",
        sa.Column(
            "never_shareable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "members",
        sa.Column(
            "fronting_private",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Self-declared 18+, captured at first grant creation. No DOB, no ID.
    op.add_column(
        "users",
        sa.Column("adult_attested_at", sa.DateTime(timezone=True), nullable=True),
    )

    # New System Safety category. Unlike the others it gates an EXPOSING
    # action rather than a destructive one.
    op.add_column(
        "systems",
        sa.Column(
            "safety_applies_to_profile_visibility",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.drop_column("systems", "safety_applies_to_profile_visibility")
    op.drop_column("users", "adult_attested_at")
    op.drop_column("members", "fronting_private")
    op.drop_column("members", "never_shareable")

    op.drop_index("ix_share_grants_due", table_name="share_grants")
    op.drop_index("uq_share_grants_one_public", table_name="share_grants")
    op.drop_table("share_grants")

    op.drop_index("ix_share_view_fields_due", table_name="share_view_fields")
    op.drop_table("share_view_fields")
    op.drop_table("share_view_groups")

    op.drop_index("ix_share_view_members_due", table_name="share_view_members")
    op.drop_table("share_view_members")

    op.drop_table("share_views")

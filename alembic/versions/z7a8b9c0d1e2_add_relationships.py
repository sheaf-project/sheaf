"""Add relationship_types, member_relationships, group_relationships

Revision ID: z7a8b9c0d1e2
Revises: y6z7a8b9c0d1
Create Date: 2026-07-12

Typed relationship graph over a system's members and groups. `RelationshipType`
is a per-system, user-defined vocabulary (name + symmetry mode + labels); the
two edge tables share it. Exactly one canonical row is stored per relationship;
the inverse is derived at read time.

Uniqueness is over the UNORDERED pair per type (no both A->B and B->A of one
type), which a plain UNIQUE(source,target,type) cannot express - hence the
functional unique index on least()/greatest(). uuid has a total, immutable
ordering so it is index-safe. A no-self-edge CheckConstraint guards each table.

CREATE TABLE takes locks only on the brand-new relations; the lock_timeout is
belt-and-braces per house style. No backfill (new tables).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID

revision: str = "z7a8b9c0d1e2"
down_revision: Union[str, None] = "y6z7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False: the types are created once by the explicit op.execute below.
# Generic sa.Enum(create_type=False) does NOT suppress the auto-create inside
# create_table (and `visibility` is shared by two tables, so it would collide);
# the postgresql.ENUM form reliably honours create_type=False.
_symmetry = postgresql.ENUM(
    "symmetric", "directional", "either",
    name="relationshipsymmetry", create_type=False,
)
_visibility = postgresql.ENUM(
    "private", name="relationshipvisibility", create_type=False,
)


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.execute(
        "CREATE TYPE relationshipsymmetry AS ENUM "
        "('symmetric', 'directional', 'either')"
    )
    op.execute("CREATE TYPE relationshipvisibility AS ENUM ('private')")

    op.create_table(
        "relationship_types",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "system_id",
            UUID(as_uuid=True),
            sa.ForeignKey("systems.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("symmetry", _symmetry, nullable=False),
        sa.Column("forward_label", sa.String(100), nullable=False),
        sa.Column("reverse_label", sa.String(100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "system_id", "name", name="uq_relationship_types_system_name"
        ),
    )

    for table, node_table, ck_name in (
        ("member_relationships", "members", "ck_member_rel_no_self"),
        ("group_relationships", "groups", "ck_group_rel_no_self"),
    ):
        op.create_table(
            table,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "system_id",
                UUID(as_uuid=True),
                sa.ForeignKey("systems.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "source_id",
                UUID(as_uuid=True),
                sa.ForeignKey(f"{node_table}.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "target_id",
                UUID(as_uuid=True),
                sa.ForeignKey(f"{node_table}.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "relationship_type_id",
                UUID(as_uuid=True),
                sa.ForeignKey("relationship_types.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "mutual", sa.Boolean(),
                nullable=False, server_default=sa.false(),
            ),
            sa.Column(
                "visibility", _visibility,
                nullable=False, server_default="private",
            ),
            sa.Column(
                "created_at", sa.DateTime(timezone=True),
                nullable=False, server_default=sa.func.now(),
            ),
            sa.CheckConstraint("source_id <> target_id", name=ck_name),
        )
        # Unordered-pair-per-type uniqueness: (A,B) and (B,A) collide.
        op.create_index(
            f"uq_{table}_unordered",
            table,
            [
                sa.text("system_id"),
                sa.text("relationship_type_id"),
                sa.text("least(source_id, target_id)"),
                sa.text("greatest(source_id, target_id)"),
            ],
            unique=True,
        )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    for table in ("group_relationships", "member_relationships"):
        op.drop_index(f"uq_{table}_unordered", table_name=table)
        op.drop_table(table)
    op.drop_table("relationship_types")
    op.execute("DROP TYPE relationshipvisibility")
    op.execute("DROP TYPE relationshipsymmetry")

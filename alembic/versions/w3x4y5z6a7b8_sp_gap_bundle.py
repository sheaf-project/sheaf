"""Add custom-front flag, member emoji, and front custom status

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-05-06

Three small additive columns introduced as one bundle:

- members.is_custom_front (boolean, default false): marks a Member row as
  a non-counting fronting entity ("Asleep", "Away", "Lost time"). Excluded
  from member headcount but still selectable on the front start dialog and
  shown in the fronter list.
- members.emoji (varchar(8) nullable): a short visual identifier surfaced
  alongside or instead of the avatar fallback in badges and lists.
- fronts.custom_status (text nullable): per-fronting-period free-text
  annotation, e.g. "during a job interview". Doesn't replace bio edits.
"""

from alembic import op
import sqlalchemy as sa

revision = "w3x4y5z6a7b8"
down_revision = "v2w3x4y5z6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    member_cols = {c["name"] for c in inspector.get_columns("members")}
    if "is_custom_front" not in member_cols:
        op.add_column(
            "members",
            sa.Column(
                "is_custom_front",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "emoji" not in member_cols:
        op.add_column(
            "members",
            sa.Column("emoji", sa.String(length=8), nullable=True),
        )

    front_cols = {c["name"] for c in inspector.get_columns("fronts")}
    if "custom_status" not in front_cols:
        op.add_column(
            "fronts",
            sa.Column("custom_status", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("fronts", "custom_status")
    op.drop_column("members", "emoji")
    op.drop_column("members", "is_custom_front")

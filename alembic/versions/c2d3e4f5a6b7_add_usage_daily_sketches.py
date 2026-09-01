"""add usage_daily_sketches (durable HLL sketch backing store)

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-09-01

Durability backstop for the aggregate DAU/MAU usage metrics. Each row holds the
raw HyperLogLog register bytes for one (day, scope) - scope is "acct" or "sys".
This is aggregate OPS data, not user data: the account/system ids are
irreversibly folded into the HLL registers, so a sketch can estimate a distinct
count but can never enumerate members or answer "was id X active on day Y".

Why store the sketch BYTES and not a daily scalar count: a 30-day MAU is the
cardinality of the UNION of 30 daily sketches, which cannot be reconstructed
from summed daily counts (that double-counts returning users). Redis survives an
in-place upgrade but not an instance replace, so the mergeable sketches are
persisted here and RESTOREd before the monthly union after a replace.

Composite natural key (day, scope): the flush job UPSERTs on it. Deliberately
excluded from the Article 20 user-data export - there is nothing
user-attributable to hand back.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_daily_sketches",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("sketch", sa.LargeBinary(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("day", "scope"),
    )


def downgrade() -> None:
    op.drop_table("usage_daily_sketches")

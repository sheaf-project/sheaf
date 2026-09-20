"""usage_daily_sketches: add client_family to the key

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-09-19

The aggregate usage sketches gain a client-family dimension (web / android /
ios / watch / other) alongside the existing auth-kind split, so platform share
and cross-platform overlap can be read from the same HLL machinery that already
answers DAU/MAU. The family sketches are ADDITIVE: the per-auth-kind sketches
keep being written exactly as before, so the published DAU/MAU series do not
dip across this deploy.

`client_family` joins the composite key with '' meaning "the auth-kind sketch,
not split by family", which is what every existing row is. This ALTERs the key
in place rather than recreating the table the way the auth_kind migration did:
that one had nothing worth keeping under the old shape, whereas here every
existing row is still a valid sketch under the new one, and dropping them would
throw away up to 30 days of MAU recoverability for no reason. The table is a
few hundred rows at most, so the primary-key rebuild is instantaneous; the
lock_timeout is there on principle (ACCESS EXCLUSIVE is ACCESS EXCLUSIVE).

Still aggregate ops data with nothing user-attributable in it, still excluded
from the Article 20 export.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e0f1a2b3c4d5"
down_revision: str | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column(
        "usage_daily_sketches",
        sa.Column(
            "client_family",
            sa.String(length=16),
            nullable=False,
            server_default="",
        ),
    )
    op.drop_constraint(
        "usage_daily_sketches_pkey", "usage_daily_sketches", type_="primary"
    )
    op.create_primary_key(
        "usage_daily_sketches_pkey",
        "usage_daily_sketches",
        ["day", "scope", "auth_kind", "client_family"],
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    # Family rows cannot exist under the old key without colliding with the
    # auth-kind rows for the same (day, scope, auth_kind); drop them first.
    # They are regenerated from Redis on the next flush if the column comes
    # back, and are only ever a copy of what Redis holds anyway.
    op.execute("DELETE FROM usage_daily_sketches WHERE client_family <> ''")
    op.drop_constraint(
        "usage_daily_sketches_pkey", "usage_daily_sketches", type_="primary"
    )
    op.create_primary_key(
        "usage_daily_sketches_pkey",
        "usage_daily_sketches",
        ["day", "scope", "auth_kind"],
    )
    op.drop_column("usage_daily_sketches", "client_family")

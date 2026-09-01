"""Add the exposure-raised value to the security_event_type enum

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-08-30

One value for the whole public-profiles exposure surface: raising system privacy
to public (the master switch), turning a shared view's exposure flag on, adding a
member/field/group to an already-shared view, and raising a member, group,
relationship-edge or custom-field definition to public. Until now every one of
those loosenings landed with no durable trail, so a hijacked session could widen
who can see a system with nothing recorded. The event's `outcome` column carries
`staged` / `immediate` / `activated` and its `detail` a `source` discriminator,
so a staged raise and its later finalize-sweep activation correlate and the
master switch stays filterable - one value rather than one per site.

`security_event_type` is a native Postgres enum, so the value is added with
ALTER TYPE ... ADD VALUE, which cannot run inside a transaction block - hence the
autocommit block. The call sites tolerate a missing value (record_security_event
is best-effort and never raises), so this is a safe additive migration.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_VALUES = ("exposure_raised",)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in _NEW_VALUES:
            op.execute(
                f"ALTER TYPE security_event_type ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    # Postgres has no DROP VALUE for an enum; removing a value means recreating
    # the type and rewriting every dependent column, which is not worth it for
    # additive values. Downgrade is a no-op (matches the repo convention for
    # enum-value adds, e.g. b3c4d5e6f7a8_add_security_event_share_grant_types).
    pass

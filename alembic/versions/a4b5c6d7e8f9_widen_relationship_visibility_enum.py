"""Widen the relationshipvisibility enum to the full privacy vocabulary

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-08-09

``relationshipvisibility`` shipped with a single value, ``private``, reserved
for exactly this moment: relationship edges get their own per-edge privacy
level so an edge can be published without publishing every edge. The two
missing values are the ones ``PrivacyLevel`` already uses everywhere else
(``friends``, ``public``), so after this the column speaks the same vocabulary
as ``members.privacy`` while keeping its own type name and its own default.

``relationshipvisibility`` is a native Postgres enum, so each value is added
with ALTER TYPE ... ADD VALUE, which cannot run inside a transaction block -
hence the autocommit block. Purely additive: every existing row stays
``private``, which is still the default and still the tightest setting.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a4b5c6d7e8f9"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NEW_VALUES = ("friends", "public")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in _NEW_VALUES:
            op.execute(
                "ALTER TYPE relationshipvisibility ADD VALUE IF NOT EXISTS "
                f"'{value}'"
            )


def downgrade() -> None:
    # Postgres has no DROP VALUE for an enum; removing a value means recreating
    # the type and rewriting every dependent column, which is not worth it for
    # additive values. Downgrade is a no-op (matches the repo convention for
    # enum-value adds, e.g. a8b9c0d1e2f3_add_security_event_stepup_types).
    pass

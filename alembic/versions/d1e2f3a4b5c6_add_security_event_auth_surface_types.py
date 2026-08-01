"""Add auth-surface values to the security_event_type enum

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-08-01

Three account-security events that previously left no durable trail: the
one-way "I am 18 or older" attestation (irreversible, and it unlocks share
grants); the refresh-token reuse kill (a consumed refresh token presented
outside the rotation grace window - the closest thing to a token-theft signal
we have, and until now a completely silent session revocation); and a sync
data export served to an API key, which is a sanctioned use case but the one
that most needs an after-the-fact trail once a key is known to have leaked.

`security_event_type` is a native Postgres enum, so each value is added with
ALTER TYPE ... ADD VALUE, which cannot run inside a transaction block - hence
the autocommit block. The call sites tolerate a missing value
(record_security_event is best-effort and never raises), so this is a safe
additive migration.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_VALUES = (
    "adult_attestation",
    "refresh_reuse",
    "data_export",
)


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
    # enum-value adds, e.g. a8b9c0d1e2f3_add_security_event_stepup_types).
    pass

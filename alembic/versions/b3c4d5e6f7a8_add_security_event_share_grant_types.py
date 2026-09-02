"""Add share-grant values to the security_event_type enum

Revision ID: b3c4d5e6f7a8
Revises: a0b1c2d3e4f5
Create Date: 2026-08-24

Three share-grant lifecycle events that previously left no durable trail:
publishing a view (the highest-risk act a live session can take on the share
surface), rotating a link token, and revoking a grant. Until now a hijacked
session could publish, rotate, or revoke with nothing recorded; these give the
owner an IP/UA-stamped trail to audit, matching the adult-attestation event.

`security_event_type` is a native Postgres enum, so each value is added with
ALTER TYPE ... ADD VALUE, which cannot run inside a transaction block - hence
the autocommit block. The call sites tolerate a missing value
(record_security_event is best-effort and never raises), so this is a safe
additive migration.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "a0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_VALUES = (
    "share_grant_created",
    "share_grant_rotated",
    "share_grant_revoked",
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
    # enum-value adds, e.g. e2f3a4b5c6d7_add_security_event_auth_surface_types).
    pass

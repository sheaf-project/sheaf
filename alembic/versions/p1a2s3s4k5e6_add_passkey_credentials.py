"""Add passkey_credentials table

Revision ID: p1a2s3s4k5e6
Revises: b2a3n4n5e6r7
Create Date: 2026-09-29

One row per WebAuthn credential a user has enrolled: the public key and
what a ceremony needs to verify a signature. Schema only; nothing writes
to it yet. Nothing in it is encrypted, on purpose: a public key is public
and a credential ID is a handle the browser presents in the clear.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p1a2s3s4k5e6"
down_revision: Union[str, None] = "b2a3n4n5e6r7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "passkey_credentials",
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rp_id", sa.String(length=253), nullable=False),
        sa.Column(
            "transports",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("aaguid", sa.UUID(), nullable=True),
        sa.Column("backup_eligible", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("backup_state", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("nickname", sa.String(length=128), nullable=True),
        sa.Column("created_ip", sa.String(length=45), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id"),
    )
    op.create_index(
        "ix_passkey_credentials_user_id", "passkey_credentials", ["user_id"],
    )
    op.create_index(
        "ix_passkey_credentials_credential_id",
        "passkey_credentials",
        ["credential_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_passkey_credentials_credential_id", table_name="passkey_credentials")
    op.drop_index("ix_passkey_credentials_user_id", table_name="passkey_credentials")
    op.drop_table("passkey_credentials")

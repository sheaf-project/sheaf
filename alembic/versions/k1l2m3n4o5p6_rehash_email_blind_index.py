"""Re-hash email_hash rows with keyed HMAC blind index

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-04-24 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k1l2m3n4o5p6"
down_revision: Union[str, None] = "j0k1l2m3n4o5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Decrypt each user's email and re-compute email_hash using the new
    keyed HMAC blind index. Runs in a single transaction — if decryption or
    re-hashing fails partway, the whole migration rolls back.

    Requires the same encryption key that was used to write the ciphertexts;
    this runs inside the app container where SHEAF_ENCRYPTION_KEY is already
    loaded.
    """
    from sheaf.crypto import blind_index, decrypt

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, email FROM users")).fetchall()

    for row in rows:
        plaintext = decrypt(row.email)
        new_hash = blind_index(plaintext)
        bind.execute(
            sa.text("UPDATE users SET email_hash = :h WHERE id = :id"),
            {"h": new_hash, "id": row.id},
        )


def downgrade() -> None:
    """Revert to plain SHA-256 blind indexes.

    We intentionally do not keep the old unkeyed implementation around in
    code, so downgrade reproduces it inline.
    """
    import hashlib

    from sheaf.crypto import decrypt

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, email FROM users")).fetchall()

    for row in rows:
        plaintext = decrypt(row.email).strip().lower()
        old_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        bind.execute(
            sa.text("UPDATE users SET email_hash = :h WHERE id = :id"),
            {"h": old_hash, "id": row.id},
        )

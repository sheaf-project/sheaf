"""Encrypt pending_actions.target_label and fronting_member_names at rest

Revision ID: u2v3w4x5y6z7
Revises: t1u2v3w4x5y6
Create Date: 2026-07-01

PendingAction is a transient row created for System Safety destructive-action
grace windows. It stored DECRYPTED user content in two unencrypted columns
that would land in any DB dump taken during the grace window:

  - target_label (member names, journal titles, poll questions, message
    previews)
  - fronting_member_names (a list of decrypted member names)

Everything else in Sheaf is encrypted at rest; these were the gap. This
migration relaxes both columns to Text and encrypts the existing values in
place, so they match the field-encryption model used by bios / journals /
fronts. The app now writes ciphertext on every insert (see
queue_pending_action) and decrypts defensively on read.

Runs in the app container where SHEAF_ENCRYPTION_KEY is loaded, so it can
call sheaf.crypto directly (same precedent as
o5p6q7r8s9t0_encrypt_member_and_journal_content). The table is small and
transient, so a row-by-row backfill is cheap.

- target_label: String(200) -> Text (ciphertext is longer than 200 chars).
- fronting_member_names: JSONB -> Text, holding encrypt(json.dumps(list)).
  Its server_default '[]' no longer makes sense for encrypted text and is
  dropped; the app supplies the value on every write.
- fronting_member_ids stays JSONB - opaque UUIDs are not sensitive content.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "u2v3w4x5y6z7"
down_revision: Union[str, None] = "t1u2v3w4x5y6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    import json

    from sheaf.crypto import encrypt

    bind = op.get_bind()

    # Relax the column types BEFORE writing ciphertext into them. target_label
    # widens to Text; fronting_member_names flips JSONB -> Text via a cast that
    # renders each stored list as its JSON text form (e.g. '["Alice", "Bob"]').
    # The server_default is dropped - the app sets the value on every write.
    op.alter_column(
        "pending_actions",
        "target_label",
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.alter_column(
        "pending_actions",
        "fronting_member_names",
        type_=sa.Text(),
        postgresql_using="fronting_member_names::text",
        server_default=None,
        existing_nullable=False,
    )

    # Encrypt existing rows. Idempotent: a row that already decrypts is skipped
    # so a re-run doesn't double-encrypt.
    rows = bind.execute(
        sa.text(
            "SELECT id, target_label, fronting_member_names FROM pending_actions"
        )
    ).fetchall()
    for row in rows:
        if _looks_like_ciphertext(row.target_label):
            continue
        try:
            names_list = json.loads(row.fronting_member_names)
        except Exception:
            names_list = []
        bind.execute(
            sa.text(
                "UPDATE pending_actions SET target_label = :t, "
                "fronting_member_names = :n WHERE id = :id"
            ),
            {
                "t": encrypt(row.target_label),
                "n": encrypt(json.dumps(names_list)),
                "id": row.id,
            },
        )


def downgrade() -> None:
    """Reverse the type changes, best-effort decrypting back to plaintext.

    Same caveat as upgrade - must run in the app container with the same key
    that wrote the ciphertexts. Where a value cannot be decrypted (a legacy
    plaintext row) the stored value is kept as-is. This is inherently lossy in
    the general case; it is only meaningful for a same-key round trip.
    """
    import json

    from sheaf.crypto import decrypt

    bind = op.get_bind()

    # Decrypt while the columns are still Text, so the values are valid for the
    # subsequent narrowing / JSONB cast.
    rows = bind.execute(
        sa.text(
            "SELECT id, target_label, fronting_member_names FROM pending_actions"
        )
    ).fetchall()
    for row in rows:
        try:
            plain_label = decrypt(row.target_label)[:200]
        except Exception:
            plain_label = row.target_label[:200]
        try:
            names_list = json.loads(decrypt(row.fronting_member_names))
        except Exception:
            try:
                names_list = json.loads(row.fronting_member_names)
            except Exception:
                names_list = []
        bind.execute(
            sa.text(
                "UPDATE pending_actions SET target_label = :t, "
                "fronting_member_names = :n WHERE id = :id"
            ),
            {"t": plain_label, "n": json.dumps(names_list), "id": row.id},
        )

    op.alter_column(
        "pending_actions",
        "target_label",
        type_=sa.String(length=200),
        existing_nullable=False,
    )
    op.alter_column(
        "pending_actions",
        "fronting_member_names",
        type_=postgresql.JSONB(),
        postgresql_using="fronting_member_names::jsonb",
        server_default="[]",
        existing_nullable=False,
    )


def _looks_like_ciphertext(value: str | None) -> bool:
    """Heuristic: try to decrypt. A successful decrypt means the row is already
    migrated (libsodium AEAD verification fails on plaintext that merely looks
    like base64)."""
    if not value or not isinstance(value, str):
        return False
    from sheaf.crypto import decrypt

    try:
        decrypt(value)
        return True
    except Exception:
        return False

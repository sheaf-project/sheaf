"""Encrypt member name/description, journal/revision title+body,
custom_field_values.value, and add Member.name_hash blind index.

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-04-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "o5p6q7r8s9t0"
down_revision: Union[str, None] = "n4o5p6q7r8s9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Encrypt sensitive content + add Member.name_hash blind index.

    Runs in a single transaction inside the app container, where
    SHEAF_ENCRYPTION_KEY is loaded — so we can call sheaf.crypto directly
    against existing rows. If anything fails, the whole migration rolls back.

    Length expansion: ciphertext is base64(nonce + ct + tag) which is roughly
    4/3 the binary size, so a 100-char plaintext name becomes ~165 chars of
    ciphertext. We relax the SQLAlchemy column types to unbounded String, so
    existing tables only need their length constraint dropped where one was
    declared. Postgres VARCHAR(N) → VARCHAR is a no-op for stored data.
    """
    import json

    from sheaf.crypto import blind_index, encrypt

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ---- Schema changes ---------------------------------------------------

    # Drop length constraints on encrypted columns (ciphertext is longer than
    # the plaintext bound). Postgres handles VARCHAR(N) → VARCHAR in place.
    op.alter_column("members", "name", type_=sa.String(), existing_nullable=False)
    op.alter_column("members", "description", type_=sa.Text(), existing_nullable=True)
    op.alter_column("journal_entries", "title", type_=sa.String(), existing_nullable=True)
    op.alter_column("content_revisions", "title", type_=sa.String(), existing_nullable=True)

    # Add Member.name_hash. server_default empty string for the nullable=False
    # constraint to take while we backfill below.
    members_cols = {c["name"] for c in inspector.get_columns("members")}
    if "name_hash" not in members_cols:
        op.add_column(
            "members",
            sa.Column(
                "name_hash",
                sa.String(64),
                nullable=False,
                server_default="",
            ),
        )
        op.create_index("ix_members_name_hash", "members", ["name_hash"])

    # ---- Backfill ---------------------------------------------------------

    # Members: encrypt name + description, populate name_hash. Skip rows whose
    # `name` already looks like our base64 ciphertext (idempotent re-runs).
    rows = bind.execute(
        sa.text("SELECT id, name, description FROM members")
    ).fetchall()
    for row in rows:
        # Detection heuristic: ciphertext starts with base64-url chars and is
        # >= 32 chars (24-byte nonce alone is 32 base64 chars). Plaintext names
        # are bounded at 100 chars and may match base64 by accident, so also
        # try to decrypt — if it succeeds we treat the row as already migrated.
        if _looks_like_ciphertext(row.name):
            continue
        new_name = encrypt(row.name)
        new_hash = blind_index(row.name)
        new_description = (
            encrypt(row.description) if row.description is not None else None
        )
        bind.execute(
            sa.text(
                "UPDATE members SET name = :n, name_hash = :h, "
                "description = :d WHERE id = :id"
            ),
            {
                "n": new_name,
                "h": new_hash,
                "d": new_description,
                "id": row.id,
            },
        )

    # Journal entries: encrypt title + body.
    je_rows = bind.execute(
        sa.text("SELECT id, title, body FROM journal_entries")
    ).fetchall()
    for row in je_rows:
        if _looks_like_ciphertext(row.body):
            continue
        new_title = encrypt(row.title) if row.title is not None else None
        new_body = encrypt(row.body) if row.body else encrypt("")
        bind.execute(
            sa.text(
                "UPDATE journal_entries SET title = :t, body = :b WHERE id = :id"
            ),
            {"t": new_title, "b": new_body, "id": row.id},
        )

    # Content revisions: encrypt title + body.
    cr_rows = bind.execute(
        sa.text("SELECT id, title, body FROM content_revisions")
    ).fetchall()
    for row in cr_rows:
        if _looks_like_ciphertext(row.body):
            continue
        new_title = encrypt(row.title) if row.title is not None else None
        new_body = encrypt(row.body) if row.body else encrypt("")
        bind.execute(
            sa.text(
                "UPDATE content_revisions SET title = :t, body = :b WHERE id = :id"
            ),
            {"t": new_title, "b": new_body, "id": row.id},
        )

    # Custom field values: encrypt the JSON-serialised plaintext into a JSON
    # string at the JSONB column. After: column holds a JSON string whose
    # value is the ciphertext token; before: any JSON shape.
    cfv_rows = bind.execute(
        sa.text("SELECT id, value FROM custom_field_values")
    ).fetchall()
    for row in cfv_rows:
        if row.value is None:
            continue
        # Already-encrypted rows are stored as a JSON string (ciphertext).
        if isinstance(row.value, str) and _looks_like_ciphertext(row.value):
            continue
        new_value = encrypt(json.dumps(row.value))
        bind.execute(
            sa.text("UPDATE custom_field_values SET value = :v WHERE id = :id"),
            {"v": json.dumps(new_value), "id": row.id},
        )


def downgrade() -> None:
    """Reverse: decrypt back to plaintext, drop name_hash.

    Same caveat as upgrade — must run in the app container with the same
    encryption key that wrote the ciphertexts. Rolls back length constraints
    to their pre-encryption bounds.
    """
    import json

    from sheaf.crypto import decrypt

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Decrypt members.name + description.
    rows = bind.execute(
        sa.text("SELECT id, name, description FROM members")
    ).fetchall()
    for row in rows:
        plain_name = decrypt(row.name)[:100]
        plain_description = (
            decrypt(row.description) if row.description is not None else None
        )
        bind.execute(
            sa.text("UPDATE members SET name = :n, description = :d WHERE id = :id"),
            {"n": plain_name, "d": plain_description, "id": row.id},
        )

    # Decrypt journal_entries title + body.
    je_rows = bind.execute(
        sa.text("SELECT id, title, body FROM journal_entries")
    ).fetchall()
    for row in je_rows:
        plain_title = decrypt(row.title)[:200] if row.title is not None else None
        plain_body = decrypt(row.body) if row.body else ""
        bind.execute(
            sa.text(
                "UPDATE journal_entries SET title = :t, body = :b WHERE id = :id"
            ),
            {"t": plain_title, "b": plain_body, "id": row.id},
        )

    # Decrypt content_revisions title + body.
    cr_rows = bind.execute(
        sa.text("SELECT id, title, body FROM content_revisions")
    ).fetchall()
    for row in cr_rows:
        plain_title = decrypt(row.title)[:200] if row.title is not None else None
        plain_body = decrypt(row.body) if row.body else ""
        bind.execute(
            sa.text(
                "UPDATE content_revisions SET title = :t, body = :b WHERE id = :id"
            ),
            {"t": plain_title, "b": plain_body, "id": row.id},
        )

    # Decrypt custom_field_values.value back into JSONB.
    cfv_rows = bind.execute(
        sa.text("SELECT id, value FROM custom_field_values")
    ).fetchall()
    for row in cfv_rows:
        if row.value is None:
            continue
        if not isinstance(row.value, str):
            continue
        plain = json.loads(decrypt(row.value))
        bind.execute(
            sa.text("UPDATE custom_field_values SET value = :v WHERE id = :id"),
            {"v": json.dumps(plain), "id": row.id},
        )

    # Drop the blind-index column + index.
    members_cols = {c["name"] for c in inspector.get_columns("members")}
    if "name_hash" in members_cols:
        op.drop_index("ix_members_name_hash", table_name="members")
        op.drop_column("members", "name_hash")

    # Restore length constraints.
    op.alter_column("members", "name", type_=sa.String(100), existing_nullable=False)
    op.alter_column(
        "journal_entries", "title", type_=sa.String(200), existing_nullable=True
    )
    op.alter_column(
        "content_revisions", "title", type_=sa.String(200), existing_nullable=True
    )


def _looks_like_ciphertext(value: str | None) -> bool:
    """Heuristic: try to decrypt. If it succeeds the row is already migrated.

    Plaintext that happens to be valid base64 will still fail at the libsodium
    AEAD verification step, so a successful decrypt is a strong signal.
    """
    if not value or not isinstance(value, str):
        return False
    from sheaf.crypto import decrypt

    try:
        decrypt(value)
        return True
    except Exception:
        return False

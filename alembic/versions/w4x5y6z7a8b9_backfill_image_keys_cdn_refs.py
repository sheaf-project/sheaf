"""Backfill journal/revision image_keys for legacy CDN-URL image embeds

Revision ID: w4x5y6z7a8b9
Revises: v3w4x5y6z7a8
Create Date: 2026-07-04

JournalEntry.image_keys and ContentRevision.image_keys are pre-extracted at
write time and read (without decrypting the body) by the orphan-file cleanup.
They were extracted with a narrow matcher that only recognised the
`![...](/v1/files/...)` embed form, so any body that embedded an image by its
legacy CDN-hostname URL (`{s3_public_url}/<key>`, the form pre-CDN-fix rows
store) has an *incomplete* image_keys column: the referenced key is missing.

The 2026-07-03 orphan-cleanup incident was the avatar/bio side of this same
narrow-matcher bug. The extractor has since been unified to route every embed
through the canonical resolver (recognising all three internal forms), but the
already-persisted image_keys columns still carry the old, incomplete extraction
- so a corrected cleanup would still treat those blobs as orphaned and reap
them. This re-extracts image_keys for every existing row using the now-unified
extractor, closing the gap before the cleanup job is re-enabled.

Pure data backfill: no schema change, so no ACCESS EXCLUSIVE lock and no
lock_timeout dance. Runs in the app container where SHEAF_ENCRYPTION_KEY is
loaded, exactly like the o5p6q7r8s9t0 content-encryption migration - so it can
decrypt each body and call the real extractor, guaranteeing the backfill agrees
with runtime. Idempotent: the extractor is deterministic, so a re-run only
touches rows whose stored keys still differ (none, on a second pass). A body
that fails to decrypt (key drift / corruption - which would already break the
app's own reads) is left untouched rather than wedging the deploy.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w4x5y6z7a8b9"
down_revision: Union[str, None] = "v3w4x5y6z7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables carrying a plaintext image_keys column derived from an encrypted body.
_TABLES = ("journal_entries", "content_revisions")

_CHUNK = 1000


def _backfill_table(bind, table: str) -> int:
    """Re-extract image_keys for one table. Returns the number of rows changed.

    Keyset-paginates by primary key so memory stays bounded even if edit
    history is large. Only rows whose recomputed key set differs from what is
    stored are written, so the common case (a row with no CDN-URL embed) costs
    a decrypt + compare and no UPDATE.
    """
    # Deferred: importing the extractor pulls in sheaf.files/config, which is
    # fine inside the app container the migration runs in, and ensures the
    # backfill uses the exact same matcher as runtime writes.
    from sheaf.crypto import decrypt
    from sheaf.services.markdown import extract_image_keys

    changed = 0
    last_id = None
    while True:
        if last_id is None:
            rows = bind.execute(
                sa.text(
                    f"SELECT id, body, image_keys FROM {table} "
                    f"ORDER BY id LIMIT {_CHUNK}"
                )
            ).fetchall()
        else:
            rows = bind.execute(
                sa.text(
                    f"SELECT id, body, image_keys FROM {table} "
                    f"WHERE id > :last ORDER BY id LIMIT {_CHUNK}"
                ),
                {"last": last_id},
            ).fetchall()
        if not rows:
            break
        for row in rows:
            last_id = row.id
            try:
                plaintext = decrypt(row.body)
            except Exception:
                # Undecryptable body: skip rather than abort the whole backfill.
                continue
            recomputed = extract_image_keys(plaintext)
            current = row.image_keys
            if isinstance(current, str):
                current = json.loads(current)
            if sorted(current or []) == recomputed:
                continue
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET image_keys = CAST(:keys AS JSONB) "
                    f"WHERE id = :id"
                ),
                {"keys": json.dumps(recomputed), "id": row.id},
            )
            changed += 1
    return changed


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        _backfill_table(bind, table)


def downgrade() -> None:
    # Re-extraction is a strict correction of a derived column; there is no
    # meaningful older value to restore, and reintroducing the incomplete
    # extraction would only re-arm the over-deletion bug. No-op.
    pass

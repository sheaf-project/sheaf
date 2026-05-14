"""Storage helpers for import-job payloads.

Uploaded import files (PluralKit dumps, Tupperbox JSON, etc.) need to
live somewhere between the HTTP upload and the runner picking the job
up. Reusing the existing file-storage abstraction means selfhosters'
configured backend (local-fs or S3) handles both; no separate path,
no Postgres bytea bloat in backups.

Payloads land under the `imports/` prefix with a UUID-derived key and
are cleaned up when the job reaches a terminal state, or by the
orphan-sweep job for anything left behind by a crashed runner.
"""

from __future__ import annotations

import contextlib
import uuid

from sheaf.storage import get_storage

# All import payloads share this prefix in the storage backend, so a
# self-hoster with S3 can apply lifecycle rules / IAM policies against
# the whole import-payload corpus rather than the whole bucket.
IMPORT_PAYLOAD_PREFIX = "imports/"


def make_payload_key(job_id: uuid.UUID, filename: str) -> str:
    """Compose the storage key for an uploaded import payload.

    Includes the job UUID for uniqueness and a sanitized filename
    suffix so an operator browsing the storage backend can tell what
    each blob is without cracking the row.
    """
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[:64]
    return f"{IMPORT_PAYLOAD_PREFIX}{job_id}/{safe}"


async def put_payload(key: str, data: bytes, content_type: str = "application/json") -> None:
    """Store a payload blob. The returned URL is discarded — we look up
    by key (the storage backend is internal to the runner)."""
    await get_storage().put(key, data, content_type)


async def get_payload(key: str) -> bytes | None:
    return await get_storage().get(key)


async def delete_payload(key: str) -> None:
    """Best-effort cleanup. Backend exceptions are swallowed because
    the job row is the source of truth for whether an import 'happened';
    a leftover payload blob is harmless and the orphan sweep will
    eventually catch it."""
    # Don't let storage cleanup take down a finalize path.
    with contextlib.suppress(Exception):
        await get_storage().delete(key)

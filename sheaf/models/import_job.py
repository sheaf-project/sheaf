"""One row per user-initiated import run.

Replaces the old inline-result pattern. Every import (PluralKit file,
PluralKit API fetch, Tupperbox, SimplyPlural, Sheaf re-import) now
enqueues an `ImportJob`, returns 202 immediately, and the user polls
or watches the detail page for status + counts + per-record events.

The job runner claims rows where status = 'pending' with FOR UPDATE
SKIP LOCKED, so this is forward-compatible with multi-worker rollout
without a separate leader-election layer.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class ImportJobSource(enum.StrEnum):
    PLURALKIT_FILE = "pluralkit_file"
    PLURALKIT_API = "pluralkit_api"
    TUPPERBOX_FILE = "tupperbox_file"
    SIMPLYPLURAL_FILE = "simplyplural_file"
    SHEAF_FILE = "sheaf_file"
    PLURALSPACE_FILE = "pluralspace_file"
    PRISM_FILE = "prism_file"


class ImportJobStatus(enum.StrEnum):
    # Awaiting pickup by the runner.
    PENDING = "pending"
    # Runner has claimed and is processing.
    RUNNING = "running"
    # Finished cleanly (may still contain per-record errors in `events`).
    COMPLETE = "complete"
    # Aborted at the file / schema / unrecoverable level. `last_error` is set.
    FAILED = "failed"
    # User cancelled before the runner picked it up.
    CANCELLED = "cancelled"


class ImportJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "import_jobs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ImportJobStatus.PENDING.value
    )

    # Client-supplied dedup token. Submitting the same key twice for the
    # same user returns the original job row instead of a new one, so
    # double-click on the upload button doesn't create two imports.
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # Storage key for the uploaded payload (file-based imports), or NULL
    # for API-based imports where the credential / config is carried in
    # payload_metadata instead. Cleared once the job reaches a terminal
    # state to free storage.
    payload_storage_key: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Source-specific config: options dict, encrypted PK API token,
    # selected member ids, etc. Cleared on terminal state for any
    # credential-bearing fields.
    payload_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Running tallies, e.g.
    # {"members_imported": N, "members_failed": M, "switches_imported": ...}.
    # Updated periodically while the job runs so the UI shows live progress.
    counts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # Per-record warnings and errors:
    # [{"level": "warning"|"error", "stage": str, "record_ref": str|None,
    #   "message": str}, ...]
    # Capped at ~10k entries — beyond that a "log truncated" marker is
    # appended and further events are dropped.
    events: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # FOR UPDATE SKIP LOCKED bookkeeping. claimed_by is the worker id
    # for crash recovery / observability.
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Soft-delete: user-archived terminal jobs are hidden from the
    # default history list but stay queryable.
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_import_jobs_user_idempotency"
        ),
        # Claim query: pending jobs, oldest first.
        Index(
            "ix_import_jobs_pending",
            "created_at",
            postgresql_where="status = 'pending'",
        ),
        # History list: user's most-recent jobs.
        Index(
            "ix_import_jobs_user_history",
            "user_id",
            "created_at",
        ),
    )

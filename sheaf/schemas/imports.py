"""Pydantic schemas for the async import job runner.

A single set of request/response shapes describes every import source.
Source-specific options (PK selected member ids, TB user-key, etc.)
ride along under `options: dict` because each importer's option schema
is independent and validated by the handler itself when it deserializes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sheaf.models.import_job import ImportJobSource, ImportJobStatus

# `extra="forbid"` everywhere user-controlled — catch typos and refuse
# stowaway fields rather than ignoring them silently. The defensive
# logging value of "your JSON had `option_id` not `options_id`, here's
# the 422" beats the tiny convenience of accepting either.
_strict = ConfigDict(extra="forbid")


# --- Request schemas --------------------------------------------------------


class ImportFileCreateRequest(BaseModel):
    """Form fields submitted alongside the multipart `file` upload.

    Carried as separate form fields (not a JSON body) because the file
    rides on the multipart envelope and FastAPI doesn't merge the two
    cleanly. The actual file is `UploadFile = File(...)` on the handler.
    """

    model_config = _strict

    source: Literal[
        ImportJobSource.PLURALKIT_FILE,
        ImportJobSource.TUPPERBOX_FILE,
        ImportJobSource.SIMPLYPLURAL_FILE,
        ImportJobSource.SHEAF_FILE,
        ImportJobSource.SHEAF_ARCHIVE,
        ImportJobSource.PLURALSPACE_FILE,
        ImportJobSource.PRISM_FILE,
    ]
    idempotency_key: uuid.UUID
    # Source-specific options as JSON string in the form field. Parsed
    # and validated by the source's importer when it deserializes; we
    # store it raw so a new option doesn't require a schema bump here.
    options: dict[str, Any] | None = None


class ImportApiCreateRequest(BaseModel):
    """Credential-based imports (currently PluralKit API).

    The credential field (`pk_token` for PK) is encrypted at rest in
    payload_metadata while the job runs, and wiped at finalize time.
    """

    model_config = _strict

    source: Literal[ImportJobSource.PLURALKIT_API]
    idempotency_key: uuid.UUID
    pk_token: str = Field(min_length=1, max_length=128)
    options: dict[str, Any] | None = None


# --- Response schemas -------------------------------------------------------


class ImportJobEvent(BaseModel):
    """One entry from the events JSONB array — surfaced unchanged to
    the UI so it can render the per-record warnings / errors table.

    `record_ref` is source-specific (PK HID, TB tupper id, member
    display name, ...). When set, the UI can render it as a clickable
    pointer; when null, the event is general (parse-level, schema-level)
    and just gets shown with its message.
    """

    model_config = ConfigDict(extra="allow")  # forward-compat

    level: Literal["info", "warning", "error"]
    stage: str
    message: str
    record_ref: str | None = None


class ImportJobRead(BaseModel):
    """Full user-visible state of one import job."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: ImportJobSource
    status: ImportJobStatus
    counts: dict[str, int] = Field(default_factory=dict)
    events: list[ImportJobEvent] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: str | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ImportJobSummary(BaseModel):
    """Lighter shape for the history list — drops the events array
    which can be 10k entries long. Detail view fetches the full record
    via /v1/imports/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: ImportJobSource
    status: ImportJobStatus
    counts: dict[str, int] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime


class ImportJobList(BaseModel):
    items: list[ImportJobSummary]
    # Cursor pagination, matches the fronts-history pattern. Null when
    # there's nothing more behind the last item.
    next_cursor: str | None = None

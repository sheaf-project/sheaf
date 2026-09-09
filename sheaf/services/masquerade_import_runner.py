"""Async runner handler for Masquerade file imports.

Wrap-pattern handler (see project_future_work.md "Deep per-record
instrumentation"): the defensive parse + hard-failure surfacing +
counts + warning-events all land here, but the per-record member walk
still happens inside `masquerade_import.run_import`, which skips
malformed rows with a tallied warning rather than emitting a
per-record error event. Deeper instrumentation is a logged follow-up.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.schemas.masquerade_import import MasqueradeImportOptions
from sheaf.services.import_parsing import (
    ImportPayloadError,
    expect_dict,
    parse_options,
    safe_json_loads,
)
from sheaf.services.import_runner import (
    append_event,
    load_user_system,
    register_handler,
    update_counts,
)
from sheaf.services.import_storage import get_payload
from sheaf.services.masquerade_import import preview as masquerade_preview
from sheaf.services.masquerade_import import run_import as masquerade_run_import

logger = logging.getLogger("sheaf.imports.masquerade")


async def handle_masquerade_file(job: ImportJob, db: AsyncSession) -> None:
    """Run a Masquerade-file import for a claimed ImportJob."""
    if job.payload_storage_key is None:
        raise ImportPayloadError(
            "Masquerade file job has no payload - was the upload step skipped?"
        )

    blob = await get_payload(job.payload_storage_key)
    if blob is None:
        raise ImportPayloadError(
            "Masquerade file payload missing from storage - "
            "the blob may have been swept by orphan cleanup"
        )

    append_event(
        job,
        level="info",
        stage="parse",
        message=f"parsed {len(blob)} bytes of payload",
    )

    parsed = expect_dict(safe_json_loads(blob), descriptor="Masquerade export")
    # A well-formed Masquerade export always has a `profiles` array; any
    # other shape is a wrong-format upload, not an empty system, so refuse
    # it cleanly instead of "importing" nothing.
    if not isinstance(parsed.get("profiles"), list):
        raise ImportPayloadError(
            "Masquerade export must contain a `profiles` array"
        )

    insum = masquerade_preview(parsed)
    append_event(
        job,
        level="info",
        stage="parse",
        message=f"export contained: {insum.member_count} profiles",
    )

    options = parse_options(job.payload_metadata, MasqueradeImportOptions)
    system = await load_user_system(db, job.user_id)

    result = await masquerade_run_import(parsed, options, system, db)

    update_counts(
        job,
        members_imported=result.members_imported,
        members_skipped=result.members_skipped,
        members_updated=result.members_updated,
        members_privacy_skipped=result.members_privacy_skipped,
    )
    for warning in result.warnings:
        append_event(job, level="warning", stage="import", message=warning)
    append_event(
        job,
        level="info",
        stage="import",
        message=f"imported {result.members_imported} members",
    )


register_handler(ImportJobSource.MASQUERADE_FILE.value, handle_masquerade_file)

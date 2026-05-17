"""Async runner handler for Tupperbox file imports.

Wrap-pattern handler (see project_future_work.md "Deep per-record
instrumentation"): the defensive parse + hard-failure surfacing +
counts + warning-events all land here, but the per-record member walk
still happens inside the existing `tb_import.run_import`, which skips
malformed rows silently rather than emitting a per-record error event.
Deeper instrumentation is a logged follow-up.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.schemas.tb_import import TBImportOptions
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
from sheaf.services.tb_import import run_import as tb_run_import

logger = logging.getLogger("sheaf.imports.tb")


async def handle_tupperbox_file(job: ImportJob, db: AsyncSession) -> None:
    """Run a Tupperbox-file import for a claimed ImportJob."""
    if job.payload_storage_key is None:
        raise ImportPayloadError(
            "Tupperbox file job has no payload — was the upload step skipped?"
        )

    blob = await get_payload(job.payload_storage_key)
    if blob is None:
        raise ImportPayloadError(
            "Tupperbox file payload missing from storage — "
            "the blob may have been swept by orphan cleanup"
        )

    append_event(
        job,
        level="info",
        stage="parse",
        message=f"parsed {len(blob)} bytes of payload",
    )

    parsed = expect_dict(safe_json_loads(blob), descriptor="Tupperbox export")
    options = parse_options(job.payload_metadata, TBImportOptions)
    system = await load_user_system(db, job.user_id)

    result = await tb_run_import(parsed, options, system, db)

    update_counts(
        job,
        members_imported=result.members_imported,
        groups_imported=result.groups_imported,
    )
    for warning in result.warnings:
        append_event(job, level="warning", stage="import", message=warning)
    append_event(
        job,
        level="info",
        stage="import",
        message=(
            f"imported {result.members_imported} members, "
            f"{result.groups_imported} groups"
        ),
    )


register_handler(ImportJobSource.TUPPERBOX_FILE.value, handle_tupperbox_file)

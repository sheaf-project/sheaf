"""Async runner handler for SimplyPlural file imports.

Wrap-pattern handler (see project_future_work.md "Deep per-record
instrumentation"): defensive parse + hard-failure surfacing + counts +
warning-events land here, but the per-record member / custom-front /
group / front walk still happens inside the existing
`sp_import.run_import`. Deeper per-record instrumentation is a logged
follow-up — SP's run_import is a ~230-line monolith and restructuring
it is its own task.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.schemas.sp_import import SPImportOptions
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
from sheaf.services.sp_import import run_import as sp_run_import

logger = logging.getLogger("sheaf.imports.sp")


async def handle_simplyplural_file(job: ImportJob, db: AsyncSession) -> None:
    """Run a SimplyPlural-file import for a claimed ImportJob."""
    if job.payload_storage_key is None:
        raise ImportPayloadError(
            "SimplyPlural file job has no payload — was the upload step skipped?"
        )

    blob = await get_payload(job.payload_storage_key)
    if blob is None:
        raise ImportPayloadError(
            "SimplyPlural file payload missing from storage — "
            "the blob may have been swept by orphan cleanup"
        )

    append_event(
        job,
        level="info",
        stage="parse",
        message=f"parsed {len(blob)} bytes of payload",
    )

    parsed = expect_dict(safe_json_loads(blob), descriptor="SimplyPlural export")
    options = parse_options(job.payload_metadata, SPImportOptions)
    system = await load_user_system(db, job.user_id)

    result = await sp_run_import(parsed, options, system, db)

    update_counts(
        job,
        members_imported=result.members_imported,
        custom_fronts_imported=result.custom_fronts_imported,
        members_skipped=result.members_skipped,
        members_updated=result.members_updated,
        fronts_imported=result.fronts_imported,
        fronts_skipped=result.fronts_skipped,
        groups_imported=result.groups_imported,
        groups_skipped=result.groups_skipped,
        custom_fields_imported=result.custom_fields_imported,
        custom_fields_skipped=result.custom_fields_skipped,
        notes_skipped=result.notes_skipped,
    )
    for warning in result.warnings:
        append_event(job, level="warning", stage="import", message=warning)
    append_event(
        job,
        level="info",
        stage="import",
        message=(
            f"imported {result.members_imported} members, "
            f"{result.custom_fronts_imported} custom fronts, "
            f"{result.fronts_imported} front intervals, "
            f"{result.groups_imported} groups, "
            f"{result.custom_fields_imported} custom fields"
        ),
    )


register_handler(ImportJobSource.SIMPLYPLURAL_FILE.value, handle_simplyplural_file)

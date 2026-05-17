"""Async runner handler for Sheaf native re-imports.

Wrap-pattern handler (see project_future_work.md). Defensive parse +
hard-failure surfacing + counts + warning-events land here; the
per-record walk stays inside the existing `sheaf_import.run_import`.

`sheaf_import.run_import` takes keyword options rather than an options
object, so the handler unpacks the validated SheafImportOptions model
into kwargs.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.schemas.sheaf_import import SheafImportOptions
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
from sheaf.services.sheaf_import import run_import as sheaf_run_import

logger = logging.getLogger("sheaf.imports.sheaf")


async def handle_sheaf_file(job: ImportJob, db: AsyncSession) -> None:
    """Run a Sheaf native re-import for a claimed ImportJob."""
    if job.payload_storage_key is None:
        raise ImportPayloadError(
            "Sheaf file job has no payload — was the upload step skipped?"
        )

    blob = await get_payload(job.payload_storage_key)
    if blob is None:
        raise ImportPayloadError(
            "Sheaf file payload missing from storage — "
            "the blob may have been swept by orphan cleanup"
        )

    append_event(
        job,
        level="info",
        stage="parse",
        message=f"parsed {len(blob)} bytes of payload",
    )

    parsed = expect_dict(safe_json_loads(blob), descriptor="Sheaf export")
    options = parse_options(job.payload_metadata, SheafImportOptions)
    system = await load_user_system(db, job.user_id)

    result = await sheaf_run_import(
        parsed,
        system,
        db,
        system_profile=options.system_profile,
        member_ids=options.member_ids,
        fronts=options.fronts,
        groups=options.groups,
        tags=options.tags,
        custom_fields=options.custom_fields,
    )

    update_counts(
        job,
        members_imported=result.members_imported,
        fronts_imported=result.fronts_imported,
        groups_imported=result.groups_imported,
        tags_imported=result.tags_imported,
        custom_fields_imported=result.custom_fields_imported,
    )
    for warning in result.warnings:
        append_event(job, level="warning", stage="import", message=warning)
    append_event(
        job,
        level="info",
        stage="import",
        message=(
            f"imported {result.members_imported} members, "
            f"{result.fronts_imported} fronts, "
            f"{result.groups_imported} groups, "
            f"{result.tags_imported} tags, "
            f"{result.custom_fields_imported} custom fields"
        ),
    )


register_handler(ImportJobSource.SHEAF_FILE.value, handle_sheaf_file)

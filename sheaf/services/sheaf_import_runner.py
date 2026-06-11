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
        conflict_strategy=options.conflict_strategy,
        system_profile=options.system_profile,
        member_ids=options.member_ids,
        fronts=options.fronts,
        groups=options.groups,
        tags=options.tags,
        custom_fields=options.custom_fields,
        journals=options.journals,
        messages=options.messages,
        polls=options.polls,
        reminders=options.reminders,
        notifications=options.notifications,
    )

    update_counts(
        job,
        members_imported=result.members_imported,
        members_skipped=result.members_skipped,
        members_updated=result.members_updated,
        fronts_imported=result.fronts_imported,
        fronts_skipped=result.fronts_skipped,
        groups_imported=result.groups_imported,
        groups_skipped=result.groups_skipped,
        tags_imported=result.tags_imported,
        tags_skipped=result.tags_skipped,
        custom_fields_imported=result.custom_fields_imported,
        custom_fields_skipped=result.custom_fields_skipped,
        journals_imported=result.journals_imported,
        journals_skipped=result.journals_skipped,
        revisions_imported=result.revisions_imported,
        revisions_skipped=result.revisions_skipped,
        messages_imported=result.messages_imported,
        messages_skipped=result.messages_skipped,
        polls_imported=result.polls_imported,
        polls_skipped=result.polls_skipped,
        reminders_imported=result.reminders_imported,
        reminders_skipped=result.reminders_skipped,
        channels_imported=result.channels_imported,
        channels_skipped=result.channels_skipped,
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
            f"{result.custom_fields_imported} custom fields, "
            f"{result.journals_imported} journals, "
            f"{result.revisions_imported} revisions, "
            f"{result.messages_imported} messages, "
            f"{result.polls_imported} polls, "
            f"{result.reminders_imported} reminders, "
            f"{result.channels_imported} notification channels"
        ),
    )


register_handler(ImportJobSource.SHEAF_FILE.value, handle_sheaf_file)

"""Async runner handler for PluralSpace imports.

Pulls the uploaded zip from storage, opens it, parses manifest +
data, runs the importer, and writes counts + warnings + a summary
event onto the job row.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.models.user import User
from sheaf.schemas.pluralspace_import import PluralspaceImportOptions
from sheaf.services.import_parsing import ImportPayloadError, parse_options
from sheaf.services.import_runner import (
    append_event,
    load_user_system,
    register_handler,
    update_counts,
)
from sheaf.services.import_storage import get_payload
from sheaf.services.pluralspace_import import parse_export_async, run_import

logger = logging.getLogger("sheaf.imports.pluralspace")


async def handle_pluralspace_file(job: ImportJob, db: AsyncSession) -> None:
    if job.payload_storage_key is None:
        raise ImportPayloadError(
            "PluralSpace job has no payload — was the upload step skipped?"
        )

    blob = await get_payload(job.payload_storage_key)
    if blob is None:
        raise ImportPayloadError(
            "PluralSpace payload missing from storage — "
            "the blob may have been swept by orphan cleanup"
        )

    append_event(
        job,
        level="info",
        stage="parse",
        message=f"parsed {len(blob)} bytes of export zip",
    )

    parsed = await parse_export_async(blob)
    options = parse_options(job.payload_metadata, PluralspaceImportOptions)
    system = await load_user_system(db, job.user_id)
    user = await db.get(User, job.user_id)
    if user is None:
        raise ImportPayloadError("user vanished mid-import")

    result = await run_import(
        parsed,
        system,
        user,
        db,
        conflict_strategy=options.conflict_strategy,
        system_profile=options.system_profile,
        member_ids=options.member_ids,
        custom_fronts=options.custom_fronts,
        member_avatars=options.member_avatars,
        roles_as_tags=options.roles_as_tags,
        groups=options.groups,
        custom_fields=options.custom_fields,
        fronts=options.fronts,
        journal_entries=options.journal_entries,
        chat_messages=options.chat_messages,
        polls=options.polls,
    )

    update_counts(
        job,
        members_imported=result.members_imported,
        custom_fronts_imported=result.custom_fronts_imported,
        members_skipped=result.members_skipped,
        members_updated=result.members_updated,
        avatars_imported=result.avatars_imported,
        tags_imported=result.tags_imported,
        groups_imported=result.groups_imported,
        custom_fields_imported=result.custom_fields_imported,
        fronts_imported=result.fronts_imported,
        fronts_skipped=result.fronts_skipped,
        journals_imported=result.journals_imported,
        journals_skipped=result.journals_skipped,
        messages_imported=result.messages_imported,
        messages_skipped=result.messages_skipped,
        polls_imported=result.polls_imported,
        polls_skipped=result.polls_skipped,
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
            f"{result.avatars_imported} avatars, "
            f"{result.tags_imported} tags (roles), "
            f"{result.groups_imported} groups, "
            f"{result.custom_fields_imported} custom fields, "
            f"{result.fronts_imported} fronts, "
            f"{result.journals_imported} journal entries, "
            f"{result.messages_imported} chat messages, "
            f"{result.polls_imported} polls"
        ),
    )


register_handler(ImportJobSource.PLURALSPACE_FILE.value, handle_pluralspace_file)

"""Async runner handler for Sheaf export-with-images archive imports.

Wrap-pattern handler: payload fetch, defensive zip parse (off the event
loop), options parse, system + user load, then delegate to
`sheaf_archive_import.run_import`, which wraps the native JSON walk
with the blob restore. Counts and warnings land on the job; hard
failures raise and the runner finalises the job as failed (the archive
service scrubs any blobs it wrote before re-raising).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.models.user import User
from sheaf.schemas.sheaf_import import SheafArchiveImportOptions
from sheaf.services.import_parsing import ImportPayloadError, parse_options
from sheaf.services.import_runner import (
    append_event,
    load_user_system,
    register_handler,
    update_counts,
)
from sheaf.services.import_storage import get_payload
from sheaf.services.sheaf_archive_import import parse_archive_async, run_import

logger = logging.getLogger("sheaf.imports.sheaf_archive")


async def handle_sheaf_archive(job: ImportJob, db: AsyncSession) -> None:
    """Run a Sheaf archive (export-with-images zip) import."""
    if job.payload_storage_key is None:
        raise ImportPayloadError(
            "Sheaf archive job has no payload - was the upload step skipped?"
        )

    blob = await get_payload(job.payload_storage_key)
    if blob is None:
        raise ImportPayloadError(
            "Sheaf archive payload missing from storage - "
            "the blob may have been swept by orphan cleanup"
        )

    parsed = await parse_archive_async(blob)
    append_event(
        job,
        level="info",
        stage="parse",
        message=(
            f"parsed {len(blob)} bytes of archive "
            f"({len(parsed.image_keys)} bundled image(s))"
        ),
    )

    options = parse_options(job.payload_metadata, SheafArchiveImportOptions)
    system = await load_user_system(db, job.user_id)
    user = await db.get(User, job.user_id)
    if user is None:
        raise ImportPayloadError("user vanished mid-import")

    result = await run_import(
        parsed,
        system,
        user,
        db,
        images=options.images,
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

    base = result.base
    update_counts(
        job,
        members_imported=base.members_imported,
        members_skipped=base.members_skipped,
        members_updated=base.members_updated,
        fronts_imported=base.fronts_imported,
        groups_imported=base.groups_imported,
        tags_imported=base.tags_imported,
        custom_fields_imported=base.custom_fields_imported,
        journals_imported=base.journals_imported,
        revisions_imported=base.revisions_imported,
        messages_imported=base.messages_imported,
        polls_imported=base.polls_imported,
        reminders_imported=base.reminders_imported,
        channels_imported=base.channels_imported,
        images_imported=result.images_imported,
    )
    for warning in result.warnings:
        append_event(job, level="warning", stage="import", message=warning)
    # One event per missing image, naming the records (by export-side id,
    # never plaintext names) whose reference was removed - so "what did I
    # actually lose?" is answerable from the import report.
    for key, sites in result.missing_images:
        append_event(
            job,
            level="warning",
            stage="images",
            message=(
                "image missing from the archive (or over the per-image "
                f"size limit); reference removed from: {sites}"[:500]
            ),
            record_ref=key[:200],
        )
    append_event(
        job,
        level="info",
        stage="import",
        message=(
            f"imported {base.members_imported} members, "
            f"{base.fronts_imported} fronts, "
            f"{base.journals_imported} journals, "
            f"{result.images_imported} images"
        ),
    )


register_handler(ImportJobSource.SHEAF_ARCHIVE.value, handle_sheaf_archive)

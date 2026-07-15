"""Async runner handler for Ampersand JSON file imports.

Wrap-pattern handler: defensive parse + hard-failure surfacing + counts
+ warning-events land here; the per-record walk is in
``ampersand_import.run_import``. Adds one concern the pure-JSON runners
don't have - the importer writes image blobs to storage, which do NOT
roll back with the DB transaction, so this handler scrubs any blob it
wrote if the import raises (mirrors ``sheaf_archive_import``).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.models.user import User
from sheaf.schemas.ampersand_import import AmpersandImportOptions
from sheaf.services.ampersand_import import preview as amp_preview
from sheaf.services.ampersand_import import run_import as amp_run_import
from sheaf.services.import_media import StoredImportImage
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
from sheaf.storage import get_storage

logger = logging.getLogger("sheaf.imports.ampersand")


async def handle_ampersand_file(job: ImportJob, db: AsyncSession) -> None:
    """Run an Ampersand-file import for a claimed ImportJob."""
    if job.payload_storage_key is None:
        raise ImportPayloadError(
            "Ampersand file job has no payload - was the upload step skipped?"
        )

    blob = await get_payload(job.payload_storage_key)
    if blob is None:
        raise ImportPayloadError(
            "Ampersand file payload missing from storage - "
            "the blob may have been swept by orphan cleanup"
        )

    append_event(
        job,
        level="info",
        stage="parse",
        message=f"parsed {len(blob)} bytes of payload",
    )

    parsed = expect_dict(safe_json_loads(blob), descriptor="Ampersand export")

    insum = amp_preview(parsed)
    append_event(
        job,
        level="info",
        stage="parse",
        message=(
            "export contained: "
            f"{insum.member_count} members, "
            f"{insum.custom_front_count} custom fronts, "
            f"{insum.system_count} systems, "
            f"{insum.front_history_count} fronting entries, "
            f"{insum.tag_count} tags, "
            f"{insum.custom_field_count} custom fields, "
            f"{insum.journal_count} journal posts, "
            f"{insum.note_count} notes, "
            f"{insum.board_message_count} board messages, "
            f"{insum.poll_count} polls, "
            f"{insum.reminder_count} reminders, "
            f"{insum.asset_count} assets"
        ),
    )

    options = parse_options(job.payload_metadata, AmpersandImportOptions)
    system = await load_user_system(db, job.user_id)
    user = await db.scalar(select(User).where(User.id == job.user_id))
    if user is None:  # pragma: no cover - job.user_id always resolves
        raise ImportPayloadError("Import job user not found.")

    stored_images: list[StoredImportImage] = []
    try:
        result = await amp_run_import(parsed, options, system, user, db, stored_images)
    except Exception:
        # Storage writes don't roll back with the DB; scrub what we wrote.
        storage = get_storage()
        for stored in stored_images:
            try:
                await storage.delete(stored.key)
            except Exception:  # pragma: no cover - best-effort scrub
                logger.warning(
                    "could not scrub blob %s after failed Ampersand import",
                    stored.key,
                )
        raise

    update_counts(
        job,
        members_imported=result.members_imported,
        custom_fronts_imported=result.custom_fronts_imported,
        members_skipped=result.members_skipped,
        members_updated=result.members_updated,
        groups_imported=result.groups_imported,
        groups_skipped=result.groups_skipped,
        tags_imported=result.tags_imported,
        custom_fields_imported=result.custom_fields_imported,
        custom_fields_skipped=result.custom_fields_skipped,
        fronts_imported=result.fronts_imported,
        fronts_skipped=result.fronts_skipped,
        journals_imported=result.journals_imported,
        notes_imported=result.notes_imported,
        messages_imported=result.messages_imported,
        polls_imported=result.polls_imported,
        reminders_imported=result.reminders_imported,
        images_imported=result.images_imported,
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
            f"{result.groups_imported} groups, "
            f"{result.fronts_imported} fronts, "
            f"{result.journals_imported + result.notes_imported} journal entries, "
            f"{result.messages_imported} board messages, "
            f"{result.polls_imported} polls, "
            f"{result.reminders_imported} reminders, "
            f"{result.images_imported} images"
        ),
    )


register_handler(ImportJobSource.AMPERSAND_FILE.value, handle_ampersand_file)

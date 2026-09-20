"""Async runner handler for BerryTree file imports.

Wrap-pattern handler: defensive parse, hard-failure surfacing, counts and
warning events land here; the per-record walk stays in
`berrytree_import.run_import`.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.schemas.berrytree_import import BTImportOptions
from sheaf.services.berrytree_import import preview as bt_preview
from sheaf.services.berrytree_import import run_import as bt_run_import
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

logger = logging.getLogger("sheaf.imports.berrytree")


async def handle_berrytree_file(job: ImportJob, db: AsyncSession) -> None:
    """Run a BerryTree-file import for a claimed ImportJob."""
    if job.payload_storage_key is None:
        raise ImportPayloadError(
            "BerryTree file job has no payload - was the upload step skipped?"
        )

    blob = await get_payload(job.payload_storage_key)
    if blob is None:
        raise ImportPayloadError(
            "BerryTree file payload missing from storage - "
            "the blob may have been swept by orphan cleanup"
        )

    append_event(
        job,
        level="info",
        stage="parse",
        message=f"parsed {len(blob)} bytes of payload",
    )

    parsed = expect_dict(safe_json_loads(blob), descriptor="BerryTree export")

    # Log what the export CONTAINED (input), so an admin can compare against
    # the imported/skipped counts below. "export had 50 members, imported 0"
    # is the signal that fixtures never produce but a broken real import does.
    insum = bt_preview(parsed)
    append_event(
        job,
        level="info",
        stage="parse",
        message=(
            "export contained: "
            f"{insum.member_count} members, "
            f"{insum.template_count} templates, "
            f"{insum.custom_front_count} custom fronts, "
            f"{insum.fronting_type_count} fronting types, "
            f"{insum.front_history_count} front entries, "
            f"{insum.folder_count} folders, "
            f"{insum.tag_count} tags, "
            f"{insum.custom_field_count} custom fields"
        ),
    )
    if insum.schema_version is not None:
        append_event(
            job,
            level="info",
            stage="parse",
            message=f"export schema_version {insum.schema_version}",
        )
    if insum.export_errors:
        # Count only. These strings are written by BerryTree's exporter and
        # can quote the content of the record that failed to export; the job
        # event log is stored in plaintext, so the text stays in the preview
        # response (which is not persisted) and never lands here.
        append_event(
            job,
            level="warning",
            stage="parse",
            message=(
                f"the export itself recorded {len(insum.export_errors)} "
                "section(s) it could not write, so this file was already "
                "incomplete before it reached Sheaf. The import preview "
                "shows what BerryTree said about each one."
            ),
        )

    options = parse_options(job.payload_metadata, BTImportOptions)
    system = await load_user_system(db, job.user_id)

    result = await bt_run_import(parsed, options, system, db)

    update_counts(
        job,
        members_imported=result.members_imported,
        custom_fronts_imported=result.custom_fronts_imported,
        members_skipped=result.members_skipped,
        members_updated=result.members_updated,
        members_privacy_skipped=result.members_privacy_skipped,
        templates_skipped=result.templates_skipped,
        fronts_imported=result.fronts_imported,
        fronts_skipped=result.fronts_skipped,
        groups_imported=result.groups_imported,
        groups_skipped=result.groups_skipped,
        tags_imported=result.tags_imported,
        tags_skipped=result.tags_skipped,
        custom_fields_imported=result.custom_fields_imported,
        custom_fields_skipped=result.custom_fields_skipped,
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
            f"{result.fronts_imported} front entries, "
            f"{result.groups_imported} folders, "
            f"{result.tags_imported} tags, "
            f"{result.custom_fields_imported} custom fields"
        ),
    )


register_handler(ImportJobSource.BERRYTREE_FILE.value, handle_berrytree_file)

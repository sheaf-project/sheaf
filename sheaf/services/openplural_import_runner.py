"""Async runner handler for OpenPlural v0.1 imports.

Wrap-pattern handler. Sniffs whether the payload is a bare JSON document
or an ``.openplural.zip`` bundle, translates the envelope to the native
shape (``openplural_import.to_native``), and delegates to the existing
native importer: ``sheaf_import.run_import`` for JSON,
``sheaf_archive_import.run_import`` for the bundle (which restores the
``assets/`` blobs). All the import guards therefore live in one place.

Lineage: any ``extensions.sheaf.lineage`` carried in is surfaced as an
info event. v0.1 Sheaf does not yet persist inherited lineage across a
DB round-trip (there is no column for it), so a re-export starts a fresh
Sheaf-only chain - see the limitation noted in docs/OPENPLURAL.md and
upstream issue #7.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.models.user import User
from sheaf.schemas.sheaf_import import SheafArchiveImportOptions, SheafImportOptions
from sheaf.services.import_parsing import ImportPayloadError, parse_options
from sheaf.services.import_runner import (
    append_event,
    load_user_system,
    register_handler,
    update_counts,
)
from sheaf.services.import_storage import get_payload
from sheaf.services.openplural_archive import (
    extract_residual,
    has_per_record_foreign_extensions,
    merge_residual,
    pack_residual,
    unpack_residual,
)
from sheaf.services.openplural_import import (
    build_json_import,
    inherited_lineage,
    looks_like_zip,
    parse_bundle_async,
    parse_json,
)

logger = logging.getLogger("sheaf.imports.openplural")


def _note_lineage(job: ImportJob, envelope: dict) -> None:
    lineage = inherited_lineage(envelope)
    if not lineage:
        return
    apps = ", ".join(
        str(e.get("app", "?")) for e in lineage if isinstance(e, dict)
    )
    append_event(
        job,
        level="info",
        stage="parse",
        message=(
            f"file carries lineage from {len(lineage)} prior export(s) "
            f"[{apps}]; not persisted in this release (see docs/OPENPLURAL.md)"
        ),
    )


def _preserve_residual(job: ImportJob, system, envelope: dict) -> None:
    """Capture the parts of the envelope Sheaf cannot model and park them
    (encrypted, compressed, size-capped) on the system so the next
    OpenPlural export re-emits them. Baseline (file-level + whole-section)
    passthrough; per-record foreign extensions are warned, not stored."""
    if has_per_record_foreign_extensions(envelope):
        append_event(
            job,
            level="warning",
            stage="preserve",
            message=(
                "per-record extensions from other apps were not preserved "
                "(file-level passthrough only in this release); see docs/OPENPLURAL.md"
            ),
        )
    residual = extract_residual(envelope)
    if not residual:
        return
    existing = unpack_residual(system.openplural_archive)
    merged = merge_residual(existing, residual)
    max_bytes = settings.openplural_max_preserved_mb * 1024 * 1024
    token, warning = pack_residual(merged, max_bytes=max_bytes)
    if warning:
        append_event(job, level="warning", stage="preserve", message=warning)
        return
    system.openplural_archive = token
    append_event(
        job,
        level="info",
        stage="preserve",
        message=(
            f"preserved {len(residual)} unsupported section(s) for round-trip: "
            f"{', '.join(sorted(residual))}"
        ),
    )


def _base_counts(job: ImportJob, base) -> None:
    update_counts(
        job,
        members_imported=base.members_imported,
        members_skipped=base.members_skipped,
        members_updated=base.members_updated,
        fronts_imported=base.fronts_imported,
        fronts_skipped=base.fronts_skipped,
        groups_imported=base.groups_imported,
        groups_skipped=base.groups_skipped,
        tags_imported=base.tags_imported,
        tags_skipped=base.tags_skipped,
        custom_fields_imported=base.custom_fields_imported,
        custom_fields_skipped=base.custom_fields_skipped,
        journals_imported=base.journals_imported,
        journals_skipped=base.journals_skipped,
        revisions_imported=base.revisions_imported,
        revisions_skipped=base.revisions_skipped,
        messages_imported=base.messages_imported,
        messages_skipped=base.messages_skipped,
        polls_imported=base.polls_imported,
        polls_skipped=base.polls_skipped,
        reminders_imported=base.reminders_imported,
        reminders_skipped=base.reminders_skipped,
        channels_imported=base.channels_imported,
        channels_skipped=base.channels_skipped,
    )


async def handle_openplural_file(job: ImportJob, db: AsyncSession) -> None:
    """Run an OpenPlural import (bare JSON or .openplural.zip) for a job."""
    if job.payload_storage_key is None:
        raise ImportPayloadError(
            "OpenPlural job has no payload - was the upload step skipped?"
        )
    blob = await get_payload(job.payload_storage_key)
    if blob is None:
        raise ImportPayloadError(
            "OpenPlural payload missing from storage - "
            "the blob may have been swept by orphan cleanup"
        )

    if looks_like_zip(blob):
        await _run_bundle(job, db, blob)
    else:
        await _run_json(job, db, blob)


async def _run_json(job: ImportJob, db: AsyncSession, blob: bytes) -> None:
    envelope = parse_json(blob)
    append_event(
        job,
        level="info",
        stage="parse",
        message=f"parsed {len(blob)} bytes of OpenPlural JSON",
    )
    _note_lineage(job, envelope)
    native, inline_archive = build_json_import(envelope)

    # A bare JSON that inlines its avatars (data_uri / data_base64 assets)
    # restores them through the archive importer's image pipeline instead
    # of the plain JSON path, which only keeps external avatar URLs.
    if inline_archive is not None:
        append_event(
            job,
            level="info",
            stage="parse",
            message=f"{len(inline_archive.image_keys)} inline asset(s) to restore",
        )
        await _run_archive(
            job,
            db,
            inline_archive,
            envelope,
            missing_label=(
                "inline asset could not be restored (unreadable or over the "
                "per-asset size limit)"
            ),
        )
        return

    from sheaf.services.sheaf_import import run_import as sheaf_run_import

    options = parse_options(job.payload_metadata, SheafImportOptions)
    system = await load_user_system(db, job.user_id)

    result = await sheaf_run_import(
        native,
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
    _base_counts(job, result)
    for warning in result.warnings:
        append_event(job, level="warning", stage="import", message=warning)
    _preserve_residual(job, system, envelope)
    append_event(
        job,
        level="info",
        stage="import",
        message=(
            f"imported {result.members_imported} members, "
            f"{result.fronts_imported} fronts, "
            f"{result.groups_imported} groups, "
            f"{result.tags_imported} tags, "
            f"{result.journals_imported} journals"
        ),
    )


async def _run_bundle(job: ImportJob, db: AsyncSession, blob: bytes) -> None:
    parsed, envelope = await parse_bundle_async(blob)
    append_event(
        job,
        level="info",
        stage="parse",
        message=(
            f"parsed {len(blob)} bytes of OpenPlural bundle "
            f"({len(parsed.image_keys)} bundled asset(s))"
        ),
    )
    _note_lineage(job, envelope)
    await _run_archive(
        job,
        db,
        parsed,
        envelope,
        missing_label=(
            "asset missing from the bundle (or over the per-asset size limit)"
        ),
    )


async def _run_archive(
    job: ImportJob, db: AsyncSession, parsed, envelope: dict, *, missing_label: str
) -> None:
    """Import a native dict whose images are restored from blobs (an
    ``.openplural.zip`` bundle or an inline-asset bare JSON), delegating to
    ``sheaf_archive_import`` for the image pipeline plus every section
    guard. ``missing_label`` phrases the per-asset "couldn't restore this"
    warning for the payload shape."""
    from sheaf.services.sheaf_archive_import import run_import as archive_run_import

    options = parse_options(job.payload_metadata, SheafArchiveImportOptions)
    system = await load_user_system(db, job.user_id)
    user = await db.get(User, job.user_id)
    if user is None:
        raise ImportPayloadError("user vanished mid-import")

    result = await archive_run_import(
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
    _base_counts(job, base)
    update_counts(job, images_imported=result.images_imported)
    for warning in result.warnings:
        append_event(job, level="warning", stage="import", message=warning)
    for key, sites in result.missing_images:
        append_event(
            job,
            level="warning",
            stage="images",
            message=f"{missing_label}; reference removed from: {sites}"[:500],
            record_ref=key[:200],
        )
    _preserve_residual(job, system, envelope)
    append_event(
        job,
        level="info",
        stage="import",
        message=(
            f"imported {base.members_imported} members, "
            f"{base.fronts_imported} fronts, "
            f"{base.journals_imported} journals, "
            f"{result.images_imported} assets"
        ),
    )


register_handler(ImportJobSource.OPENPLURAL_FILE.value, handle_openplural_file)

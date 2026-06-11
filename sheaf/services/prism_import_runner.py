"""Async runner handler for Prism (.prism) imports.

Pulls the uploaded envelope from storage, decrypts it with the
passphrase that's stashed in `payload_metadata.encrypted_credential`,
runs the importer, and writes counts + warnings + a summary event
back onto the job row.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import decrypt
from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.models.user import User
from sheaf.schemas.prism_import import PrismImportOptions
from sheaf.services.import_parsing import ImportPayloadError, parse_options
from sheaf.services.import_runner import (
    append_event,
    load_user_system,
    register_handler,
    update_counts,
)
from sheaf.services.import_storage import get_payload
from sheaf.services.prism_import import parse_envelope_bytes_async, run_import

logger = logging.getLogger("sheaf.imports.prism")


async def handle_prism_file(job: ImportJob, db: AsyncSession) -> None:
    if job.payload_storage_key is None:
        raise ImportPayloadError(
            "Prism job has no payload — was the upload step skipped?"
        )

    blob = await get_payload(job.payload_storage_key)
    if blob is None:
        raise ImportPayloadError(
            "Prism payload missing from storage — "
            "the blob may have been swept by orphan cleanup"
        )

    meta = job.payload_metadata or {}
    encrypted_credential = meta.get("encrypted_credential")
    if not encrypted_credential:
        raise ImportPayloadError(
            "Prism job has no passphrase — submit the .prism file together "
            "with its decryption passphrase via the credential form field."
        )
    try:
        passphrase = decrypt(encrypted_credential)
    except Exception as exc:  # noqa: BLE001 — Fernet raises InvalidToken
        raise ImportPayloadError(
            "stored passphrase ciphertext failed to decrypt; the job "
            "metadata may have been corrupted"
        ) from exc

    append_event(
        job,
        level="info",
        stage="parse",
        message=f"decrypted {len(blob)} byte PRISM1 envelope",
    )

    parsed = await parse_envelope_bytes_async(blob, passphrase)
    options = parse_options(job.payload_metadata, PrismImportOptions)
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
        member_avatars=options.member_avatars,
        roles_as_tags=options.roles_as_tags,
        member_groups=options.member_groups,
        custom_fields=options.custom_fields,
        front_sessions=options.front_sessions,
        sleep_sessions=options.sleep_sessions,
        notes=options.notes,
        polls=options.polls,
        reminders=options.reminders,
        habits=options.habits,
        conversations=options.conversations,
        member_board_posts=options.member_board_posts,
        media_attachments=options.media_attachments,
    )

    update_counts(
        job,
        members_imported=result.members_imported,
        members_skipped=result.members_skipped,
        members_updated=result.members_updated,
        avatars_imported=result.avatars_imported,
        groups_imported=result.groups_imported,
        custom_fields_imported=result.custom_fields_imported,
        custom_field_values_imported=result.custom_field_values_imported,
        fronts_imported=result.fronts_imported,
        fronts_skipped=result.fronts_skipped,
        journals_imported=result.journals_imported,
        journals_skipped=result.journals_skipped,
        messages_imported=result.messages_imported,
        messages_skipped=result.messages_skipped,
        board_posts_imported=result.board_posts_imported,
        board_posts_skipped=result.board_posts_skipped,
        polls_imported=result.polls_imported,
        polls_skipped=result.polls_skipped,
        media_attachments_imported=result.media_attachments_imported,
    )
    for warning in result.warnings:
        append_event(job, level="warning", stage="import", message=warning)
    append_event(
        job,
        level="info",
        stage="import",
        message=(
            f"imported {result.members_imported} members, "
            f"{result.avatars_imported} avatars, "
            f"{result.groups_imported} groups, "
            f"{result.custom_fields_imported} custom fields, "
            f"{result.custom_field_values_imported} custom field values, "
            f"{result.fronts_imported} fronts, "
            f"{result.journals_imported} journal entries, "
            f"{result.messages_imported} chat messages, "
            f"{result.board_posts_imported} board posts, "
            f"{result.polls_imported} polls, "
            f"{result.media_attachments_imported} media attachments"
        ),
    )


register_handler(ImportJobSource.PRISM_FILE.value, handle_prism_file)

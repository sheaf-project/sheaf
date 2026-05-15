"""Async runner handler for PluralKit file imports.

Bridges the existing per-section importer helpers (`_build_member`,
`_import_groups`, `_import_switches`) onto the ImportJob runner. The
legacy synchronous `pk_import.run_import` still exists for the older
`/v1/import/pluralkit` endpoint and lives alongside this until Phase 6
cleanup deletes the legacy surface.

Per-record errors land in `job.events` rather than aborting the
import. Hard failures (bad JSON, no system, schema-level validation)
raise; the runner's outer try/except converts that into status=failed
and a single error event.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.schemas.pk_import import PKImportOptions
from sheaf.services.import_parsing import (
    ImportPayloadError,
    expect_dict,
    safe_json_loads,
)
from sheaf.services.import_runner import append_event, register_handler, update_counts
from sheaf.services.import_storage import get_payload
from sheaf.services.pk_import import (
    _apply_system_profile,
    _build_member,
    _import_groups,
    _import_switches,
    _list,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("sheaf.imports.pk")


async def _load_user_system(db: AsyncSession, user_id) -> System:
    """Mirror of the legacy endpoint's lookup. Raises ImportPayloadError
    so the runner surfaces it as a parse-stage user-facing failure
    rather than an unhandled traceback."""
    result = await db.execute(select(System).where(System.user_id == user_id))
    system = result.scalar_one_or_none()
    if system is None:
        raise ImportPayloadError(
            "no system found on this account — create a system before importing"
        )
    return system


def _parse_options(job: ImportJob) -> PKImportOptions:
    """Pull options out of payload_metadata and Pydantic-validate them.

    Missing / null options means 'all defaults' which matches the legacy
    endpoint's no-options-passed behaviour. Invalid options is a hard
    failure (the frontend shouldn't be able to produce them, so this
    being raised in production means a client bug or hand-rolled curl)."""
    raw = (job.payload_metadata or {}).get("options")
    if raw is None or raw == {}:
        return PKImportOptions()
    if not isinstance(raw, dict):
        raise ImportPayloadError(
            f"options must be a JSON object (got {type(raw).__name__})"
        )
    try:
        return PKImportOptions.model_validate(raw)
    except ValidationError as exc:
        raise ImportPayloadError(f"invalid import options: {exc.errors()}") from exc


async def handle_pluralkit_file(job: ImportJob, db: AsyncSession) -> None:
    """Run a PluralKit-file import for a claimed ImportJob.

    Phases:
      1. Load + parse the payload (safe_json_loads guards element count)
      2. Validate options
      3. Locate the owner's system
      4. Apply system profile (optional)
      5. Walk members, building hid -> Member map; per-record errors
         land in events
      6. Walk groups (optional)
      7. Walk switches (optional, the biggest section)

    The runner commits all DB mutations once the handler returns; per-
    section flushes keep the SQLAlchemy unit of work happy without
    locking in partial state on failure (the runner does a rollback
    when the handler raises).
    """
    if job.payload_storage_key is None:
        raise ImportPayloadError(
            "PluralKit file job has no payload — was the upload step skipped?"
        )

    blob = await get_payload(job.payload_storage_key)
    if blob is None:
        raise ImportPayloadError(
            "PluralKit file payload missing from storage — "
            "the blob may have been swept by orphan cleanup"
        )

    append_event(
        job,
        level="info",
        stage="parse",
        message=f"parsed {len(blob)} bytes of payload",
    )

    parsed = expect_dict(
        safe_json_loads(blob), descriptor="PluralKit export"
    )
    options = _parse_options(job)

    system = await _load_user_system(db, job.user_id)

    if options.system_profile:
        _apply_system_profile(parsed, system)
        append_event(
            job,
            level="info",
            stage="system_profile",
            message="applied PK system fields to Sheaf system row",
        )

    # --- Members ---------------------------------------------------------

    pk_members = _list(parsed, "members")
    if options.member_ids is not None:
        wanted = set(options.member_ids)
        pk_members = [m for m in pk_members if m.get("id") in wanted]

    hid_to_member: dict[str, Member] = {}
    for pk_m in pk_members:
        # Defensive: each member runs in its own try so one bad row
        # doesn't kill the whole batch. Bad rows append an error event
        # with the HID so the user can see what got skipped.
        hid_for_event = pk_m.get("id") if isinstance(pk_m, dict) else None
        try:
            member = _build_member(pk_m, system.id)
        except Exception as exc:
            update_counts(job, members_failed=1)
            append_event(
                job,
                level="error",
                stage="members",
                message=f"failed to build member: {exc!s}"[:500],
                record_ref=str(hid_for_event) if hid_for_event else None,
            )
            continue
        if member is None:
            # _build_member returns None for unusable rows (no name, no id).
            update_counts(job, members_failed=1)
            append_event(
                job,
                level="warning",
                stage="members",
                message="member row had no usable name / id, skipped",
                record_ref=str(hid_for_event) if hid_for_event else None,
            )
            continue
        db.add(member)
        hid = str(hid_for_event or "")
        if hid:
            hid_to_member[hid] = member
        update_counts(job, members_imported=1)

    await db.flush()

    # --- Groups ----------------------------------------------------------

    if options.groups:
        try:
            count = await _import_groups(
                _list(parsed, "groups"), system.id, hid_to_member, db
            )
            update_counts(job, groups_imported=count)
            append_event(
                job,
                level="info",
                stage="groups",
                message=f"imported {count} groups",
            )
        except Exception as exc:
            append_event(
                job,
                level="error",
                stage="groups",
                message=f"group import failed: {exc!s}"[:500],
            )
            raise

    # --- Switches → fronts ----------------------------------------------

    if options.front_history:
        try:
            fronts, warnings = await _import_switches(
                _list(parsed, "switches"), system.id, hid_to_member, db
            )
            update_counts(job, fronts_imported=fronts)
            append_event(
                job,
                level="info",
                stage="switches",
                message=f"imported {fronts} front intervals from switch history",
            )
            for warning in warnings:
                append_event(
                    job, level="warning", stage="switches", message=warning
                )
        except Exception as exc:
            append_event(
                job,
                level="error",
                stage="switches",
                message=f"switch import failed: {exc!s}"[:500],
            )
            raise


# Register at module-import time so importing this module from
# import_runner._register_builtin_handlers wires it up.
register_handler(ImportJobSource.PLURALKIT_FILE.value, handle_pluralkit_file)

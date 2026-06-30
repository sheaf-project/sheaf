"""Async runner handlers for PluralKit imports — file upload + API fetch.

Both sources produce the same canonical PK-export dict, so they share
one post-parse processor (`_process_pk_export`): the file handler
parses an uploaded blob, the API handler fetches the same shape live
from PluralKit, then both walk members / groups / switches identically.

Bridges the per-section importer helpers (`build_member`,
`import_groups`, `import_switches`, `apply_system_profile`,
`get_list`) in pk_import.py onto the ImportJob runner.

Per-record errors land in `job.events` rather than aborting. Hard
failures (bad JSON, no system, PK API rejection) raise; the runner's
outer try/except converts that into status=failed.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import decrypt
from sheaf.models.import_job import ImportJob, ImportJobSource
from sheaf.models.member import Member
from sheaf.schemas.pk_import import PKImportOptions
from sheaf.services.import_dedup import (
    candidate_key,
    count_new_members,
    load_member_match_index,
    resolve_member,
)
from sheaf.services.import_limits import ClampReport
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
from sheaf.services.member_limits import enforce_import_member_cap
from sheaf.services.pk_api import PKApiError, fetch_export
from sheaf.services.pk_import import (
    apply_system_profile,
    build_member,
    get_list,
    import_groups,
    import_switches,
)

logger = logging.getLogger("sheaf.imports.pk")


async def _process_pk_export(
    job: ImportJob,
    db: AsyncSession,
    parsed: dict,
    options: PKImportOptions,
) -> None:
    """Walk a parsed PK-export dict into the owner's system.

    Shared by the file and API handlers — by the time this runs the
    source-specific bit (read a blob vs fetch from the API) is done and
    `parsed` is the canonical export shape either way.

    Phases: locate system -> system profile (optional) -> members
    (per-record errors -> events) -> groups (optional) -> switches
    (optional, the biggest section). The runner commits everything once
    the handler returns; per-section flushes keep the unit of work
    happy without locking in partial state on failure.
    """
    system = await load_user_system(db, job.user_id)

    # Tally any field/list the import clamps to its schema cap, so the user
    # gets a "N member names were shortened" warning in the job event log
    # (matching the warn-before-import preview prediction). Threaded through
    # every section helper that constructs a row.
    report = ClampReport()

    if options.system_profile:
        apply_system_profile(parsed, system, report=report)
        append_event(
            job,
            level="info",
            stage="system_profile",
            message="applied PK system fields to Sheaf system row",
        )

    # --- Members ---------------------------------------------------------

    pk_members = get_list(parsed, "members")
    if options.member_ids is not None:
        wanted = set(options.member_ids)
        pk_members = [m for m in pk_members if m.get("id") in wanted]

    # Build all candidates first (pure construction, no DB writes), so
    # the member-cap check below counts only the rows this run would
    # actually CREATE. Under skip/update a pure re-import adds nothing
    # and must not trip the cap.
    candidates: list[tuple[Member, str]] = []
    for pk_m in pk_members:
        # Defensive: each member runs in its own try so one bad row
        # doesn't kill the whole batch. Bad rows append an error event
        # with the HID so the user can see what got skipped.
        hid_for_event = pk_m.get("id") if isinstance(pk_m, dict) else None
        try:
            member = build_member(pk_m, system.id, report=report)
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
            # build_member returns None for unusable rows (no name, no id).
            update_counts(job, members_failed=1)
            append_event(
                job,
                level="warning",
                stage="members",
                message="member row had no usable name / id, skipped",
                record_ref=str(hid_for_event) if hid_for_event else None,
            )
            continue
        candidates.append((member, str(hid_for_event or "")))

    # Match against members already in the system. Hard-fail before
    # writing anything if the NEW members would blow the cap.
    index = await load_member_match_index(db, system.id)
    new_count = count_new_members(
        [candidate_key(m) for m, _ in candidates],
        index=index,
        strategy=options.conflict_strategy,
    )
    await enforce_import_member_cap(db, system, new_count)

    hid_to_member: dict[str, Member] = {}
    for member, hid in candidates:
        resolution = resolve_member(
            member, index=index, strategy=options.conflict_strategy
        )
        if resolution.disposition == "created":
            db.add(resolution.member)
            update_counts(job, members_imported=1)
        elif resolution.disposition == "updated":
            update_counts(job, members_updated=1)
        else:
            update_counts(job, members_skipped=1)
        # Downstream sections (groups, switches) link via this map, so it
        # must point at the resolved row whether created, skipped, or
        # updated.
        if hid:
            hid_to_member[hid] = resolution.member

    await db.flush()

    # --- Groups ----------------------------------------------------------

    if options.groups:
        try:
            count, group_skipped = await import_groups(
                get_list(parsed, "groups"),
                system.id,
                hid_to_member,
                db,
                conflict_strategy=options.conflict_strategy,
                report=report,
            )
            update_counts(job, groups_imported=count, groups_skipped=group_skipped)
            append_event(
                job,
                level="info",
                stage="groups",
                message=f"imported {count} groups ({group_skipped} already present)",
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
            fronts, fronts_skipped, warnings = await import_switches(
                get_list(parsed, "switches"),
                system.id,
                hid_to_member,
                db,
                conflict_strategy=options.conflict_strategy,
            )
            update_counts(
                job, fronts_imported=fronts, fronts_skipped=fronts_skipped
            )
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

    # --- Clamp warnings --------------------------------------------------

    # Surface any field/list that hit a schema cap during this run. One event
    # per distinct clamped field, e.g. "3 member names will be shortened ...".
    for warning in report.to_warnings():
        append_event(job, level="warning", stage="limits", message=warning)


async def handle_pluralkit_file(job: ImportJob, db: AsyncSession) -> None:
    """Run a PluralKit-file import: load the stashed payload, parse it
    with the element-count guard, then hand off to the shared processor."""
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

    parsed = expect_dict(safe_json_loads(blob), descriptor="PluralKit export")
    options = parse_options(job.payload_metadata, PKImportOptions)
    await _process_pk_export(job, db, parsed, options)


async def handle_pluralkit_api(job: ImportJob, db: AsyncSession) -> None:
    """Run a PluralKit-API import: decrypt the stashed token, fetch the
    system live from PluralKit, then hand off to the shared processor.

    This is the migration that actually fixes a bug rather than just
    hardening one — the live fetch paginates switch history with rate-
    limit sleeps, which on a big system runs tens of seconds and would
    blow the HTTP request timeout on the old synchronous endpoint.
    Off the request path, it just takes as long as it takes.
    """
    meta = job.payload_metadata or {}
    encrypted = meta.get("encrypted_credential")
    if not encrypted:
        raise ImportPayloadError(
            "PluralKit API job has no stored credential — "
            "the token may have already been wiped by a prior run"
        )
    try:
        token = decrypt(encrypted)
    except Exception as exc:
        raise ImportPayloadError(
            "could not decrypt the stored PluralKit token"
        ) from exc

    options = parse_options(job.payload_metadata, PKImportOptions)

    append_event(
        job,
        level="info",
        stage="fetch",
        message="fetching system from the PluralKit API",
    )
    try:
        parsed = await fetch_export(token, include_switches=options.front_history)
    except PKApiError as exc:
        # PK API rejections (bad token, 404, rate limit, upstream 5xx)
        # are hard failures — surface the PK-provided message verbatim,
        # it's already user-readable.
        raise ImportPayloadError(f"PluralKit API: {exc}") from exc

    append_event(
        job,
        level="info",
        stage="fetch",
        message=(
            f"fetched {len(get_list(parsed, 'members'))} members, "
            f"{len(get_list(parsed, 'groups'))} groups, "
            f"{len(get_list(parsed, 'switches'))} switches"
        ),
    )
    await _process_pk_export(job, db, parsed, options)


# Register at module-import time so importing this module from
# import_runner._register_builtin_handlers wires both handlers up.
register_handler(ImportJobSource.PLURALKIT_FILE.value, handle_pluralkit_file)
register_handler(ImportJobSource.PLURALKIT_API.value, handle_pluralkit_api)

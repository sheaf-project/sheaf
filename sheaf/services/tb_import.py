"""Tupperbox data import.

Tupperbox exports are JSON files produced by the bot's `tb!export`
command. Top-level shape:

  - `tuppers` — array of tupper objects (member-equivalents)
  - `groups` — array of group objects

There's no system metadata, no fronting log, no custom fields, no
privacy model, and no per-member colour. The whole import collapses
down to creating Members + Groups and wiring the group memberships.

A handful of Tupperbox concepts have no equivalent in Sheaf and are
dropped silently to match the PluralKit importer's behaviour:

  - `brackets` / `show_brackets` — Tupperbox proxy tags; Sheaf is not a
    Discord proxy.
  - `tag` (per-tupper) — system-tag suffix appended when proxying.
  - `banner` — Sheaf has no member banner.
  - `last_used`, `posts`, `avatar` (CDN filename without URL),
    `created_at` — not modelled.

Member IDs in the export are integers; we stringify them for transport
through preview/options so the wire format matches the other importers'
string-id convention.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import blind_index, encrypt
from sheaf.models.group import Group
from sheaf.models.member import Member, group_members
from sheaf.models.system import PrivacyLevel, System
from sheaf.schemas.tb_import import (
    TBImportOptions,
    TBImportResult,
    TBPreviewMember,
    TBPreviewSummary,
)
from sheaf.services import import_limits as il
from sheaf.services.import_content_dedup import (
    ContentMatchIndex,
    PairGuard,
    load_group_index,
    load_group_member_guard,
)
from sheaf.services.import_dedup import (
    ImportConflictStrategy,
    candidate_key,
    count_new_members,
    load_member_match_index,
    resolve_member,
)
from sheaf.services.import_limits import ClampReport, clamp_str
from sheaf.services.import_parsing import sanitize_external_avatar_url
from sheaf.services.member_limits import enforce_import_member_cap

logger = logging.getLogger("sheaf.import.tb")


def _list(data: dict, key: str) -> list[dict]:
    """Get a list-typed collection from TB data, defaulting to empty."""
    value = data.get(key)
    return value if isinstance(value, list) else []


def _tupper_id(tupper: dict) -> str | None:
    """Stringify a tupper's numeric id for use as a stable key."""
    tid = tupper.get("id")
    if tid is None:
        return None
    return str(tid)


def _measure_payload(data: dict, report: ClampReport) -> None:
    """Tally which Tupperbox fields exceed the schema caps, into ``report``.

    Reads the same source keys ``run_import`` clamps (a tupper's name + nick,
    a group's name), calling the same helper with the same caps, so the
    preview's warnings match what the import would shorten. Defensive: only
    string values are measured, so a malformed upload can't raise here.
    """

    def s(value: object, cap: il.Cap) -> None:
        if isinstance(value, str):
            clamp_str(value, cap, report=report)

    for t in _list(data, "tuppers"):
        if not isinstance(t, dict):
            continue
        s(t.get("name"), il.M_NAME)
        s(t.get("nick"), il.M_DISPLAY_NAME)

    for g in _list(data, "groups"):
        if isinstance(g, dict):
            s(g.get("name"), il.GROUP_NAME)


def preview(data: dict) -> TBPreviewSummary:
    """Summarise a Tupperbox export for the user before they confirm."""
    tuppers = _list(data, "tuppers")
    groups = _list(data, "groups")

    report = ClampReport()
    _measure_payload(data, report)

    return TBPreviewSummary(
        member_count=len(tuppers),
        members=[
            TBPreviewMember(
                id=_tupper_id(t) or "",
                name=_clean_str(t.get("name")) or "unnamed",
            )
            for t in tuppers
            if _tupper_id(t) is not None
        ],
        group_count=len(groups),
        limit_warnings=report.to_warnings()
        + il.import_row_cap_warnings({"groups": len(groups)}),
    )


async def run_import(
    data: dict,
    options: TBImportOptions,
    system: System,
    db: AsyncSession,
) -> TBImportResult:
    """Import a parsed Tupperbox export into the user's system."""
    result = TBImportResult()
    warnings: list[str] = []
    # Tally over-cap fields clamped during this run; surfaced as warnings at
    # the end (the preview shows the same prediction up front).
    report = ClampReport()

    tuppers = _list(data, "tuppers")
    if options.member_ids is not None:
        wanted = set(options.member_ids)
        tuppers = [t for t in tuppers if _tupper_id(t) in wanted]

    # Build candidates first (no DB writes), so the member-cap check
    # below counts only the rows this run would actually CREATE.
    candidates: list[tuple[Member, str | None]] = []
    tuppers_no_name = 0
    for tupper in tuppers:
        member = _build_member(tupper, system.id, report)
        if member is None:
            tuppers_no_name += 1
            continue
        candidates.append((member, _tupper_id(tupper)))

    index = await load_member_match_index(db, system.id)
    new_count = count_new_members(
        [candidate_key(m) for m, _ in candidates],
        index=index,
        strategy=options.conflict_strategy,
    )
    await enforce_import_member_cap(db, system, new_count)

    # Per-import row caps (bomb protection). Beyond members, Tupperbox only
    # writes groups; hard-fail before writing if they blow the cap. Gross
    # count: dedup/skip only reduces the real write count.
    il.enforce_import_row_caps(
        {"groups": len(_list(data, "groups")) if options.groups else 0}
    )

    id_to_member: dict[str, Member] = {}
    tuppers_no_id = 0
    for member, tid in candidates:
        resolution = resolve_member(
            member, index=index, strategy=options.conflict_strategy
        )
        if resolution.disposition == "created":
            db.add(resolution.member)
            result.members_imported += 1
        elif resolution.disposition == "updated":
            result.members_updated += 1
        else:
            result.members_skipped += 1
        # Map by tupper id (when present) to the resolved row so groups
        # wire onto the right member whether created, skipped, or updated.
        if tid is None:
            tuppers_no_id += 1
        else:
            id_to_member[tid] = resolution.member

    await db.flush()

    if options.groups:
        result.groups_imported, result.groups_skipped, group_warnings = (
            await _import_groups(
                _list(data, "groups"),
                tuppers,
                system.id,
                id_to_member,
                db,
                report,
                conflict_strategy=options.conflict_strategy,
            )
        )
        warnings.extend(group_warnings)

    if tuppers_no_name:
        warnings.append(
            f"Skipped {tuppers_no_name} tupper rows with no name (malformed "
            "export row)."
        )
    if tuppers_no_id:
        warnings.append(
            f"Imported {tuppers_no_id} tuppers with no id; they came across "
            "as members but couldn't be wired into any group."
        )
    # Clamp tally goes last so a "3 member names were shortened" note follows
    # the per-record warnings in the job log.
    result.warnings = warnings + report.to_warnings()
    # See pk_import.run_import for the full rationale: `get_db`'s
    # auto-commit runs after the response is sent, which races a
    # follow-up request on slow CI. Commit explicitly here so writes
    # are visible by the time the client receives the response.
    await db.commit()
    return result


# --- Members -----------------------------------------------------------------


def _build_member(
    tupper: dict, system_id: uuid.UUID, report: ClampReport
) -> Member | None:
    """Construct a Sheaf Member from a Tupperbox tupper object.

    Returns None if the row lacks a usable name. Tupperbox has no privacy
    flags, so every imported member defaults to PRIVATE — users can flip
    individual members to public after import if they want.
    """
    plaintext_name = _clean_str(tupper.get("name"))
    if not plaintext_name:
        return None
    plaintext_name = clamp_str(plaintext_name, il.M_NAME, report=report)
    plaintext_description = _clean_str(tupper.get("description"))

    return Member(
        id=uuid.uuid4(),
        system_id=system_id,
        name=encrypt(plaintext_name),
        name_hash=blind_index(plaintext_name),
        display_name=clamp_str(
            _clean_str(tupper.get("nick")), il.M_DISPLAY_NAME, report=report
        ),
        description=encrypt(plaintext_description) if plaintext_description else None,
        pronouns=None,  # Tupperbox doesn't model pronouns.
        avatar_url=sanitize_external_avatar_url(_clean_str(tupper.get("avatar_url"))),
        color=None,  # Tupperbox doesn't model member colour.
        birthday=_normalize_birthday(tupper.get("birthday")),
        privacy=PrivacyLevel.PRIVATE,
    )


# --- Groups ------------------------------------------------------------------


async def _import_groups(
    tb_groups: list[dict],
    selected_tuppers: list[dict],
    system_id: uuid.UUID,
    id_to_member: dict[str, Member],
    db: AsyncSession,
    report: ClampReport,
    *,
    conflict_strategy: ImportConflictStrategy = ImportConflictStrategy.CREATE,
) -> tuple[int, int, list[str]]:
    """Create Sheaf Groups and wire member associations.

    Tupperbox doesn't list members on its group objects; the relationship
    is the other way round (each tupper has a `group_id`). We invert that
    mapping here, restricted to tuppers the user actually selected.

    Under skip/update a same-named existing group is reused instead of
    duplicated. Returns `(imported, skipped, warnings)` so the runner
    can fold per-section warnings into the import-detail event log.
    """
    imported = 0
    skipped = 0
    warnings: list[str] = []
    groups_no_name = 0
    groups_no_id = 0
    dedupe = conflict_strategy != ImportConflictStrategy.CREATE
    group_index = (
        await load_group_index(db, system_id) if dedupe else ContentMatchIndex()
    )
    sheaf_group_by_tbid: dict[str, Group] = {}

    for tb_g in tb_groups:
        name = _clean_str(tb_g.get("name"))
        if not name:
            groups_no_name += 1
            continue
        gid = tb_g.get("id")
        if gid is None:
            groups_no_id += 1
            continue
        name = clamp_str(name, il.GROUP_NAME, report=report)
        existing = group_index.get(name) if dedupe else None
        if existing is not None:
            sheaf_group_by_tbid[str(gid)] = existing
            skipped += 1
            continue
        group = Group(
            id=uuid.uuid4(),
            system_id=system_id,
            name=name,
            description=_clean_str(tb_g.get("description")),
        )
        db.add(group)
        group_index.register(name, group)
        sheaf_group_by_tbid[str(gid)] = group
        imported += 1

    if groups_no_name:
        warnings.append(
            f"Skipped {groups_no_name} group rows with no name."
        )
    if groups_no_id:
        warnings.append(
            f"Skipped {groups_no_id} group rows with no id."
        )
    if not sheaf_group_by_tbid:
        return imported, skipped, warnings

    await db.flush()

    # Build the group → [members] map from each tupper's group_id field.
    # The pair guard covers a reused group + skipped member and duplicate
    # pairs within the file.
    pair_guard = (
        await load_group_member_guard(db, system_id) if dedupe else PairGuard()
    )
    members_with_unknown_group = 0
    for tupper in selected_tuppers:
        gid = tupper.get("group_id")
        if gid is None:
            continue
        group = sheaf_group_by_tbid.get(str(gid))
        if group is None:
            members_with_unknown_group += 1
            continue
        tid = _tupper_id(tupper)
        if tid is None:
            continue
        member = id_to_member.get(tid)
        if member is None:
            continue
        if not pair_guard.add((group.id, member.id)):
            continue
        await db.execute(
            group_members.insert().values(group_id=group.id, member_id=member.id)
        )
    if members_with_unknown_group:
        warnings.append(
            f"Dropped {members_with_unknown_group} group references on "
            "tuppers that pointed at a group not present in the export."
        )
    return imported, skipped, warnings


# --- Helpers -----------------------------------------------------------------


def _clean_str(value: Any) -> str | None:
    """Coerce to string, strip whitespace, return None for empty."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _normalize_birthday(value: Any) -> str | None:
    """Tupperbox stores birthdays as full ISO timestamps with a year.

    Sheaf stores them as YYYY-MM-DD (or MM-DD when year-less). We just
    take the date prefix; if the prefix doesn't look like a date we drop
    the value rather than store garbage.
    """
    s = _clean_str(value)
    if not s:
        return None
    # Accept either a date-only prefix or a full ISO timestamp.
    date_part = s[:10]
    if len(date_part) != 10 or date_part[4] != "-" or date_part[7] != "-":
        return None
    return date_part

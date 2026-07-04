"""PluralKit data import.

PK exports are JSON documents at the system level. The shape is the same
for both ingestion paths — uploaded export files and live API pulls —
because `pk_api.fetch_export` stitches the live response into the export
file format before handing it to this module.

Top-level keys we use:
- `name`, `description`, `tag`, `color`, `avatar_url`, `pronouns` — system profile
- `members` — array of PK member objects (with HID `id`, `uuid`, etc.)
- `groups` — array of PK group objects (with HIDs and a `members` HID list)
- `switches` — array of `{timestamp, members: [hid, ...]}` events,
  newest-first

Switch model conversion is the only non-trivial part. PK records system
switches as point-in-time events: each switch carries the new fronter set
and supersedes the previous. Sheaf records front intervals: each Front
has a started_at, an optional ended_at, and a member set. We walk the
switch log oldest-to-newest, ending the previous Front at each new
switch's timestamp and opening a new Front with the new members. Empty
member sets correspond to "nobody fronting", so they end the previous
Front without opening a new one. Members spanning multiple consecutive
switches end up in multiple Front records; the existing
coalesce-contiguous-fronts feature reassembles them on display.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import blind_index, encrypt
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.member import Member, front_members, group_members
from sheaf.models.system import PrivacyLevel, System
from sheaf.schemas.pk_import import (
    PKPreviewMember,
    PKPreviewSummary,
)
from sheaf.services import import_limits as il
from sheaf.services.import_content_dedup import (
    ContentMatchIndex,
    PairGuard,
    front_key,
    load_front_index,
    load_group_index,
    load_group_member_guard,
    normalize_front_interval,
)
from sheaf.services.import_dedup import ImportConflictStrategy
from sheaf.services.import_limits import ClampReport, clamp_str
from sheaf.services.import_parsing import sanitize_external_avatar_url

logger = logging.getLogger("sheaf.import.pk")

# A PK HID is 5-7 lowercase alphanumeric characters. The String(8) column
# leaves a byte of slack for any future widening on PK's side.
_PKID_MAX_LEN = 8


def get_list(data: dict, key: str) -> list[dict]:
    """Get a list-typed collection from PK data, defaulting to empty."""
    value = data.get(key)
    return value if isinstance(value, list) else []


def _parse_iso(value: Any) -> datetime | None:
    """Parse a PK ISO-8601 timestamp into an aware datetime, or None."""
    if not isinstance(value, str):
        return None
    try:
        # PK uses Zulu suffix; fromisoformat accepts +00:00.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def measure_pk_payload(data: dict, report: ClampReport) -> None:
    """Tally which PK-shaped fields exceed the schema caps, into ``report``.

    Reads the same PK keys the import path clamps, calling the same
    ``clamp_str`` helpers, so the preview's warnings match what the import
    would shorten. Octocon exports route through this importer too, so they
    inherit this prediction. Defensive: only string values are measured, so a
    malformed upload can't raise here.
    """

    def s(value: object, cap: il.Cap) -> None:
        if isinstance(value, str):
            clamp_str(value, cap, report=report)

    # System profile (only filled in when the Sheaf-side field is empty, but
    # we measure unconditionally - the user can't tell from the preview which
    # fields are already set, and over-measuring is harmless here).
    s(data.get("name"), il.SYS_NAME)
    s(data.get("tag"), il.SYS_TAG)

    for m in get_list(data, "members"):
        if not isinstance(m, dict):
            continue
        s(m.get("name"), il.M_NAME)
        s(m.get("display_name"), il.M_DISPLAY_NAME)
        s(m.get("pronouns"), il.M_PRONOUNS)
        s(m.get("id"), il.M_PLURALKIT_ID)

    for g in get_list(data, "groups"):
        if isinstance(g, dict):
            s(g.get("name"), il.GROUP_NAME)


def preview(data: dict, *, switch_count_override: int | None = None) -> PKPreviewSummary:
    """Summarise a PK export for the user before they confirm the import.

    `switch_count_override` lets the live-API preview path inject a
    minimum-known count when only one page was fetched. The file path
    leaves it None so the count comes straight from the parsed JSON.
    """
    members = get_list(data, "members")
    groups = get_list(data, "groups")
    switches = get_list(data, "switches")

    timestamps = [_parse_iso(s.get("timestamp")) for s in switches]
    timestamps = [t for t in timestamps if t is not None]

    report = ClampReport()
    measure_pk_payload(data, report)

    switch_count = (
        switch_count_override if switch_count_override is not None else len(switches)
    )
    # PK switches become front-history rows on import; surface the per-import
    # front / group caps up front alongside the clamp warnings.
    limit_warnings = report.to_warnings() + il.import_row_cap_warnings(
        {"fronts": switch_count, "groups": len(groups)}
    )

    return PKPreviewSummary(
        system_name=_clean_str(data.get("name")),
        member_count=len(members),
        members=[
            PKPreviewMember(
                id=str(m.get("id") or ""),
                name=_clean_str(m.get("name")) or "unnamed",
            )
            for m in members
            if m.get("id")
        ],
        group_count=len(groups),
        switch_count=switch_count,
        earliest_switch=min(timestamps) if timestamps else None,
        latest_switch=max(timestamps) if timestamps else None,
        limit_warnings=limit_warnings,
    )


# The synchronous run_import that used to live here is gone — the
# PluralKit import now runs through the async job runner, whose handler
# (pk_import_runner.handle_pluralkit_file) calls the per-section
# helpers below directly. `preview` above is still used by the
# /v1/import/pluralkit/preview endpoint.


# --- System profile ----------------------------------------------------------


def apply_system_profile(data: dict, system: System, *, report: ClampReport) -> None:
    """Copy a few non-destructive fields from the PK system onto Sheaf's.

    We never overwrite a name the user has already set (their Sheaf system
    name takes priority), but we will fill in optional fields like tag,
    color, and avatar when they're empty on Sheaf side. PK's `description`
    is not pulled in by default — Sheaf system descriptions are heavily
    user-styled and a silent overwrite during import is the kind of thing
    that reads as a bug. A separate "also import system description"
    toggle could be added later if anyone asks.
    """
    name = _clean_str(data.get("name"))
    if name and not system.name:
        system.name = clamp_str(name, il.SYS_NAME, report=report)
    tag = _clean_str(data.get("tag"))
    if tag and not system.tag:
        system.tag = clamp_str(tag, il.SYS_TAG, report=report)
    color = _normalize_color(data.get("color"))
    if color and not system.color:
        system.color = color
    # PK avatars are external CDN URLs; the sanitizer enforces the
    # http(s)-only scheme allowlist and the instance external-image policy.
    avatar = sanitize_external_avatar_url(_clean_str(data.get("avatar_url")))
    if avatar and not system.avatar_url:
        system.avatar_url = avatar


# --- Members -----------------------------------------------------------------


def build_member(
    pk_m: dict, system_id: uuid.UUID, *, report: ClampReport
) -> Member | None:
    """Construct a Sheaf Member from a PK member object.

    Returns None if the source row lacks a usable name. Encryption,
    blind-index, length clamping, and HID clamping all live here.
    """
    plaintext_name = _clean_str(pk_m.get("name"))
    if not plaintext_name:
        return None
    plaintext_name = clamp_str(plaintext_name, il.M_NAME, report=report)
    plaintext_description = _clean_str(pk_m.get("description"))

    pk_hid = _clean_str(pk_m.get("id"))

    return Member(
        id=uuid.uuid4(),
        system_id=system_id,
        name=encrypt(plaintext_name),
        name_hash=blind_index(plaintext_name),
        display_name=clamp_str(
            _clean_str(pk_m.get("display_name")), il.M_DISPLAY_NAME, report=report
        ),
        description=encrypt(plaintext_description) if plaintext_description else None,
        pronouns=clamp_str(
            _clean_str(pk_m.get("pronouns")), il.M_PRONOUNS, report=report
        ),
        avatar_url=sanitize_external_avatar_url(_clean_str(pk_m.get("avatar_url"))),
        color=_normalize_color(pk_m.get("color")),
        birthday=_normalize_birthday(pk_m.get("birthday")),
        pluralkit_id=clamp_str(pk_hid, il.M_PLURALKIT_ID, report=report),
        privacy=_map_privacy(pk_m.get("privacy")),
    )


# --- Groups ------------------------------------------------------------------


async def import_groups(
    pk_groups: list[dict],
    system_id: uuid.UUID,
    hid_to_member: dict[str, Member],
    db: AsyncSession,
    *,
    conflict_strategy: ImportConflictStrategy = ImportConflictStrategy.CREATE,
    report: ClampReport,
) -> tuple[int, int]:
    """Create Sheaf Groups from PK groups and wire member associations.

    PK groups don't nest, so there's no parent linking pass. The members
    list inside each PK group is HIDs; we look them up in hid_to_member
    and silently drop any that the user deselected during preview.

    Under skip/update, a same-named existing group is reused instead of
    duplicated (membership still merges onto it - see
    import_content_dedup). Returns (imported, skipped).
    """
    imported = 0
    skipped = 0
    dedupe = conflict_strategy != ImportConflictStrategy.CREATE
    group_index = (
        await load_group_index(db, system_id) if dedupe else ContentMatchIndex()
    )
    sheaf_groups: list[tuple[dict, Group]] = []

    for pk_g in pk_groups:
        name = _clean_str(pk_g.get("name"))
        if not name:
            continue
        name = clamp_str(name, il.GROUP_NAME, report=report)
        existing = group_index.get(name) if dedupe else None
        if existing is not None:
            sheaf_groups.append((pk_g, existing))
            skipped += 1
            continue
        group = Group(
            id=uuid.uuid4(),
            system_id=system_id,
            name=name,
            description=_clean_str(pk_g.get("description")),
            color=_normalize_color(pk_g.get("color")),
        )
        db.add(group)
        group_index.register(name, group)
        sheaf_groups.append((pk_g, group))
        imported += 1

    if not sheaf_groups:
        return 0, skipped

    await db.flush()

    # The pair guard covers a reused group + skipped member (association
    # already in the DB) and duplicate pairs within the file.
    pair_guard = (
        await load_group_member_guard(db, system_id) if dedupe else PairGuard()
    )
    for pk_g, group in sheaf_groups:
        # PK exposes group membership in two shapes depending on endpoint:
        # - The export file inlines `members` as a list of member HIDs.
        # - The /v2/groups?with_members=true response inlines them as full
        #   member objects, in which case we still want HIDs.
        for entry in get_list(pk_g, "members"):
            hid = entry if isinstance(entry, str) else _clean_str(
                entry.get("id") if isinstance(entry, dict) else None
            )
            if not hid:
                continue
            member = hid_to_member.get(hid)
            if not member:
                continue
            if not pair_guard.add((group.id, member.id)):
                continue
            await db.execute(
                group_members.insert().values(group_id=group.id, member_id=member.id)
            )

    return imported, skipped


# --- Switches → fronts -------------------------------------------------------


async def import_switches(
    pk_switches: list[dict],
    system_id: uuid.UUID,
    hid_to_member: dict[str, Member],
    db: AsyncSession,
    *,
    conflict_strategy: ImportConflictStrategy = ImportConflictStrategy.CREATE,
) -> tuple[int, int, list[str]]:
    """Convert PK switch events into Sheaf Front intervals.

    Walking oldest-to-newest, each switch opens a Front with the
    resolved member set that the next switch closes; empty switches
    (= nobody fronting) preserve the gap. Under skip/update an interval
    that already exists - same start, end, and member set - is skipped,
    so re-importing the same switch history doesn't double it.

    Returns `(fronts_imported, fronts_skipped, warnings)`. The most
    common warning is "this switch references a member that wasn't
    imported", typically because the user deselected that member at
    preview time.
    """
    if not pk_switches:
        return 0, 0, []

    parsed: list[tuple[datetime, list[str]]] = []
    skipped_no_ts = 0
    for sw in pk_switches:
        ts = _parse_iso(sw.get("timestamp"))
        if ts is None:
            skipped_no_ts += 1
            continue
        members = sw.get("members") or []
        if not isinstance(members, list):
            members = []
        parsed.append((ts, [str(m) for m in members if m]))

    parsed.sort(key=lambda item: item[0])

    warnings: list[str] = []
    if skipped_no_ts:
        warnings.append(
            f"Skipped {skipped_no_ts} switch entries with no timestamp."
        )

    imported = 0
    skipped = 0
    fronts_swapped = 0
    missing_hids: set[str] = set()
    dedupe = conflict_strategy != ImportConflictStrategy.CREATE
    front_index = (
        await load_front_index(db, system_id) if dedupe else ContentMatchIndex()
    )

    # Each switch opens an interval that the NEXT switch closes (any
    # switch, including an empty "nobody fronting" one); the final
    # switch's interval stays open. Intervals are deterministic from the
    # sorted list, so a re-import reproduces the same
    # (started, ended, members) keys and dedups exactly.
    for i, (ts, hids) in enumerate(parsed):
        if not hids:
            continue

        members = []
        for hid in hids:
            member = hid_to_member.get(hid)
            if member is None:
                missing_hids.add(hid)
                continue
            members.append(member)

        if not members:
            # All referenced members were filtered out; treat as "nobody fronting".
            continue

        ended_at = parsed[i + 1][0] if i + 1 < len(parsed) else None
        ts, ended_at, swapped = normalize_front_interval(ts, ended_at)
        if swapped:
            fronts_swapped += 1
        if dedupe:
            fkey = front_key(ts, ended_at, {m.id for m in members})
            if front_index.get(fkey) is not None:
                skipped += 1
                continue
            front_index.register(fkey)

        front = Front(
            id=uuid.uuid4(),
            system_id=system_id,
            started_at=ts,
            ended_at=ended_at,
        )
        db.add(front)
        await db.flush()
        for member in members:
            await db.execute(
                front_members.insert().values(front_id=front.id, member_id=member.id)
            )
        imported += 1

    if missing_hids:
        warnings.append(
            f"Skipped {len(missing_hids)} member references in switch history "
            "that pointed to members not selected for import."
        )
    if fronts_swapped:
        warnings.append(
            f"Adjusted {fronts_swapped} switch "
            f"{'interval' if fronts_swapped == 1 else 'intervals'} whose end "
            "time was before the start time (swapped the two)."
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


def _normalize_color(value: Any) -> str | None:
    """Coerce a PK color (`6c89bb` style) to Sheaf's `#6c89bb` format."""
    s = _clean_str(value)
    if not s:
        return None
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6:
        return None
    if not all(c in "0123456789abcdefABCDEF" for c in s):
        return None
    return f"#{s.lower()}"


def _normalize_birthday(value: Any) -> str | None:
    """Accept PK birthday formats (YYYY-MM-DD, possibly 0004-MM-DD as no-year).

    PK uses 0004 as a sentinel year for birthdays where the year is not
    known. Sheaf supports MM-DD too, so we collapse the sentinel form down.
    """
    s = _clean_str(value)
    if not s:
        return None
    # Year-less sentinel: PK stores no-year birthdays as 0004-MM-DD.
    if s.startswith("0004-") and len(s) == 10:
        return s[5:]  # MM-DD
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    if len(s) == 5 and s[2] == "-":
        return s
    return s[:10]


def _map_privacy(privacy: Any) -> PrivacyLevel:
    """Collapse PK's per-field privacy map to Sheaf's tri-level enum.

    PK has fine-grained `name_privacy`, `description_privacy`, etc., each
    `public` or `private`. Sheaf has a single `privacy` field on the
    member with three values. We use PK's `visibility` field (the
    overall member-level flag) when present, falling back to the
    most-restrictive field-level value if not. Anything that isn't
    explicitly `public` is treated as private — PK's middle ground
    `friends_only`-style states don't exist there, so we don't need to
    map onto Sheaf's `friends` value automatically.
    """
    if not isinstance(privacy, dict):
        # PK export defaults to "private" historically when no privacy
        # block is present.
        return PrivacyLevel.PRIVATE
    visibility = privacy.get("visibility")
    if visibility == "public":
        return PrivacyLevel.PUBLIC
    if visibility == "private":
        return PrivacyLevel.PRIVATE
    # Fallback: most-restrictive of the field-level flags.
    flags = [
        v for k, v in privacy.items()
        if k.endswith("_privacy") and isinstance(v, str)
    ]
    if flags and all(v == "public" for v in flags):
        return PrivacyLevel.PUBLIC
    return PrivacyLevel.PRIVATE

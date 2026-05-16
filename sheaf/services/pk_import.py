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

logger = logging.getLogger("sheaf.import.pk")

# A PK HID is 5-7 lowercase alphanumeric characters. The String(8) column
# leaves a byte of slack for any future widening on PK's side.
_PKID_MAX_LEN = 8


def _list(data: dict, key: str) -> list[dict]:
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


def preview(data: dict, *, switch_count_override: int | None = None) -> PKPreviewSummary:
    """Summarise a PK export for the user before they confirm the import.

    `switch_count_override` lets the live-API preview path inject a
    minimum-known count when only one page was fetched. The file path
    leaves it None so the count comes straight from the parsed JSON.
    """
    members = _list(data, "members")
    groups = _list(data, "groups")
    switches = _list(data, "switches")

    timestamps = [_parse_iso(s.get("timestamp")) for s in switches]
    timestamps = [t for t in timestamps if t is not None]

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
        switch_count=switch_count_override if switch_count_override is not None else len(switches),
        earliest_switch=min(timestamps) if timestamps else None,
        latest_switch=max(timestamps) if timestamps else None,
    )


# The synchronous run_import that used to live here is gone — the
# PluralKit import now runs through the async job runner, whose handler
# (pk_import_runner.handle_pluralkit_file) calls the per-section
# helpers below directly. `preview` above is still used by the
# /v1/import/pluralkit/preview endpoint.


# --- System profile ----------------------------------------------------------


def _apply_system_profile(data: dict, system: System) -> None:
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
        system.name = name[:100]
    tag = _clean_str(data.get("tag"))
    if tag and not system.tag:
        system.tag = tag[:8]
    color = _normalize_color(data.get("color"))
    if color and not system.color:
        system.color = color
    avatar = _clean_str(data.get("avatar_url"))
    if avatar and not system.avatar_url:
        system.avatar_url = avatar[:500]


# --- Members -----------------------------------------------------------------


def _build_member(pk_m: dict, system_id: uuid.UUID) -> Member | None:
    """Construct a Sheaf Member from a PK member object.

    Returns None if the source row lacks a usable name. Encryption,
    blind-index, length truncation, and HID truncation all live here.
    """
    plaintext_name = _clean_str(pk_m.get("name"))
    if not plaintext_name:
        return None
    plaintext_name = plaintext_name[:100]
    plaintext_description = _clean_str(pk_m.get("description"))

    pk_hid = _clean_str(pk_m.get("id"))

    return Member(
        id=uuid.uuid4(),
        system_id=system_id,
        name=encrypt(plaintext_name),
        name_hash=blind_index(plaintext_name),
        display_name=_truncate(_clean_str(pk_m.get("display_name")), 100),
        description=encrypt(plaintext_description) if plaintext_description else None,
        pronouns=_truncate(_clean_str(pk_m.get("pronouns")), 100),
        avatar_url=_truncate(_clean_str(pk_m.get("avatar_url")), 500),
        color=_normalize_color(pk_m.get("color")),
        birthday=_normalize_birthday(pk_m.get("birthday")),
        pluralkit_id=_truncate(pk_hid, _PKID_MAX_LEN),
        privacy=_map_privacy(pk_m.get("privacy")),
    )


# --- Groups ------------------------------------------------------------------


async def _import_groups(
    pk_groups: list[dict],
    system_id: uuid.UUID,
    hid_to_member: dict[str, Member],
    db: AsyncSession,
) -> int:
    """Create Sheaf Groups from PK groups and wire member associations.

    PK groups don't nest, so there's no parent linking pass. The members
    list inside each PK group is HIDs; we look them up in hid_to_member
    and silently drop any that the user deselected during preview.
    """
    imported = 0
    sheaf_groups: list[tuple[dict, Group]] = []

    for pk_g in pk_groups:
        name = _clean_str(pk_g.get("name"))
        if not name:
            continue
        group = Group(
            id=uuid.uuid4(),
            system_id=system_id,
            name=name[:100],
            description=_clean_str(pk_g.get("description")),
            color=_normalize_color(pk_g.get("color")),
        )
        db.add(group)
        sheaf_groups.append((pk_g, group))
        imported += 1

    if not sheaf_groups:
        return 0

    await db.flush()

    for pk_g, group in sheaf_groups:
        # PK exposes group membership in two shapes depending on endpoint:
        # - The export file inlines `members` as a list of member HIDs.
        # - The /v2/groups?with_members=true response inlines them as full
        #   member objects, in which case we still want HIDs.
        for entry in _list(pk_g, "members"):
            hid = entry if isinstance(entry, str) else _clean_str(
                entry.get("id") if isinstance(entry, dict) else None
            )
            if not hid:
                continue
            member = hid_to_member.get(hid)
            if not member:
                continue
            await db.execute(
                group_members.insert().values(group_id=group.id, member_id=member.id)
            )

    return imported


# --- Switches → fronts -------------------------------------------------------


async def _import_switches(
    pk_switches: list[dict],
    system_id: uuid.UUID,
    hid_to_member: dict[str, Member],
    db: AsyncSession,
) -> tuple[int, list[str]]:
    """Convert PK switch events into Sheaf Front intervals.

    Walking oldest-to-newest, each switch:
      - ends the previous open Front at this timestamp,
      - opens a new Front with the resolved member set, unless the switch
        is empty (= nobody fronting), in which case the gap is preserved.

    Returns `(fronts_imported, warnings)`. The most common warning is
    "this switch references a member that wasn't imported", typically
    because the user deselected that member at preview time.
    """
    if not pk_switches:
        return 0, []

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
    open_front: Front | None = None
    missing_hids: set[str] = set()

    for ts, hids in parsed:
        if open_front is not None:
            open_front.ended_at = ts
            open_front = None

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

        front = Front(
            id=uuid.uuid4(),
            system_id=system_id,
            started_at=ts,
            ended_at=None,
        )
        db.add(front)
        await db.flush()
        for member in members:
            await db.execute(
                front_members.insert().values(front_id=front.id, member_id=member.id)
            )
        open_front = front
        imported += 1

    if missing_hids:
        warnings.append(
            f"Skipped {len(missing_hids)} member references in switch history "
            "that pointed to members not selected for import."
        )

    return imported, warnings


# --- Helpers -----------------------------------------------------------------


def _clean_str(value: Any) -> str | None:
    """Coerce to string, strip whitespace, return None for empty."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _truncate(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    return value[:max_len]


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

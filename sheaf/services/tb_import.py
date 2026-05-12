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


def preview(data: dict) -> TBPreviewSummary:
    """Summarise a Tupperbox export for the user before they confirm."""
    tuppers = _list(data, "tuppers")
    groups = _list(data, "groups")

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

    tuppers = _list(data, "tuppers")
    if options.member_ids is not None:
        wanted = set(options.member_ids)
        tuppers = [t for t in tuppers if _tupper_id(t) in wanted]

    id_to_member: dict[str, Member] = {}
    for tupper in tuppers:
        member = _build_member(tupper, system.id)
        if member is None:
            continue
        db.add(member)
        tid = _tupper_id(tupper)
        if tid is not None:
            id_to_member[tid] = member
        result.members_imported += 1

    await db.flush()

    if options.groups:
        result.groups_imported = await _import_groups(
            _list(data, "groups"), tuppers, system.id, id_to_member, db
        )

    result.warnings = warnings
    # See pk_import.run_import for the full rationale: `get_db`'s
    # auto-commit runs after the response is sent, which races a
    # follow-up request on slow CI. Commit explicitly here so writes
    # are visible by the time the client receives the response.
    await db.commit()
    return result


# --- Members -----------------------------------------------------------------


def _build_member(tupper: dict, system_id: uuid.UUID) -> Member | None:
    """Construct a Sheaf Member from a Tupperbox tupper object.

    Returns None if the row lacks a usable name. Tupperbox has no privacy
    flags, so every imported member defaults to PRIVATE — users can flip
    individual members to public after import if they want.
    """
    plaintext_name = _clean_str(tupper.get("name"))
    if not plaintext_name:
        return None
    plaintext_name = plaintext_name[:100]
    plaintext_description = _clean_str(tupper.get("description"))

    return Member(
        id=uuid.uuid4(),
        system_id=system_id,
        name=encrypt(plaintext_name),
        name_hash=blind_index(plaintext_name),
        display_name=_truncate(_clean_str(tupper.get("nick")), 100),
        description=encrypt(plaintext_description) if plaintext_description else None,
        pronouns=None,  # Tupperbox doesn't model pronouns.
        avatar_url=_truncate(_clean_str(tupper.get("avatar_url")), 500),
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
) -> int:
    """Create Sheaf Groups and wire member associations.

    Tupperbox doesn't list members on its group objects; the relationship
    is the other way round (each tupper has a `group_id`). We invert that
    mapping here, restricted to tuppers the user actually selected.
    """
    imported = 0
    sheaf_group_by_tbid: dict[str, Group] = {}

    for tb_g in tb_groups:
        name = _clean_str(tb_g.get("name"))
        if not name:
            continue
        gid = tb_g.get("id")
        if gid is None:
            continue
        group = Group(
            id=uuid.uuid4(),
            system_id=system_id,
            name=name[:100],
            description=_clean_str(tb_g.get("description")),
        )
        db.add(group)
        sheaf_group_by_tbid[str(gid)] = group
        imported += 1

    if not sheaf_group_by_tbid:
        return 0

    await db.flush()

    # Build the group → [members] map from each tupper's group_id field.
    for tupper in selected_tuppers:
        gid = tupper.get("group_id")
        if gid is None:
            continue
        group = sheaf_group_by_tbid.get(str(gid))
        if group is None:
            continue
        tid = _tupper_id(tupper)
        if tid is None:
            continue
        member = id_to_member.get(tid)
        if member is None:
            continue
        await db.execute(
            group_members.insert().values(group_id=group.id, member_id=member.id)
        )

    return imported


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

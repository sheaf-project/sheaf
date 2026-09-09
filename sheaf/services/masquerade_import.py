"""Masquerade data import.

Masquerade is a PluralKit-like proxy bot for Stoat. Its export is a
JSON object with a single top-level key:

  - `profiles` - array of profile objects (member-equivalents)

Each profile carries `name` (required), `display_name`, `avatar_url`,
`color`, `hidden`, and `tags` (proxy prefix/suffix pairs). There's no
system metadata, no groups, no fronting log, no custom fields - the
whole import collapses down to creating Members.

A couple of Masquerade concepts have no equivalent in Sheaf and are
dropped silently to match the PluralKit importer's behaviour:

  - `tags` - Masquerade proxy prefix/suffix pairs; Sheaf does no
    message proxying.
  - `hidden` - hides a profile from Masquerade's public listings.
    Imported members are private by default in Sheaf, so the flag
    adds nothing.

Masquerade profiles carry no id of their own (the bot keys them by
name), so we use each profile's position in the `profiles` array,
stringified, as its stable key for preview/options transport. That
matches the other importers' string-id convention, and the same file
is parsed for preview and import, so the positions line up.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import blind_index, encrypt
from sheaf.encrypted_fields import member_name_aad
from sheaf.models.member import Member
from sheaf.models.system import PrivacyLevel, System
from sheaf.schemas.masquerade_import import (
    MasqueradeImportOptions,
    MasqueradeImportResult,
    MasqueradePreviewMember,
    MasqueradePreviewSummary,
)
from sheaf.services import import_limits as il
from sheaf.services.import_dedup import (
    candidate_key,
    count_new_members,
    load_member_match_index,
    privacy_hold_warning,
    resolve_member,
)
from sheaf.services.import_limits import ClampReport, clamp_str
from sheaf.services.import_parsing import sanitize_external_avatar_url
from sheaf.services.member_limits import enforce_import_member_cap

logger = logging.getLogger("sheaf.import.masquerade")


def _profiles(data: dict) -> list[dict]:
    """The `profiles` array as a list of dict rows; missing -> []."""
    raw = data.get("profiles")
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)]
    return []


def _coerce_str(value: Any) -> str | None:
    """Plain string, or None for null / non-string. Never str()-quotes a
    non-string so member content can't leak into an error via coercion."""
    return value if isinstance(value, str) else None


def _clean_str(value: Any) -> str | None:
    """Coerce to string, strip whitespace, return None for empty."""
    s = _coerce_str(value)
    if s is None:
        return None
    s = s.strip()
    return s or None


def _normalize_color(color: object) -> str | None:
    """Normalise a colour to '#rrggbb', or None. Handles 3/6/8-hex with or
    without a leading '#'; 8-hex is treated as ARGB (drop the alpha byte)."""
    if not isinstance(color, str):
        return None
    s = color.strip().lstrip("#")
    if len(s) == 3:
        s = f"{s[0] * 2}{s[1] * 2}{s[2] * 2}"
    elif len(s) == 8:
        s = s[2:]
    if len(s) != 6 or not all(c in "0123456789abcdefABCDEF" for c in s):
        return None
    return f"#{s.lower()}"


def _measure_payload(data: dict, report: ClampReport) -> None:
    """Tally which Masquerade fields exceed the schema caps, into ``report``.

    Reads the same source keys ``run_import`` clamps (a profile's name +
    display_name), calling the same helper with the same caps, so the
    preview's warnings match what the import would shorten. Defensive: only
    string values are measured, so a malformed upload can't raise here.
    """

    def s(value: object, cap: il.Cap) -> None:
        if isinstance(value, str):
            clamp_str(value, cap, report=report)

    for p in _profiles(data):
        s(p.get("name"), il.M_NAME)
        s(p.get("display_name"), il.M_DISPLAY_NAME)


def preview(data: dict) -> MasqueradePreviewSummary:
    """Summarise a Masquerade export for the user before they confirm."""
    profiles = _profiles(data)

    report = ClampReport()
    _measure_payload(data, report)

    return MasqueradePreviewSummary(
        member_count=len(profiles),
        members=[
            MasqueradePreviewMember(
                id=str(idx),
                name=_clean_str(p.get("name")) or "unnamed",
            )
            for idx, p in enumerate(profiles)
        ],
        limit_warnings=report.to_warnings(),
    )


async def run_import(
    data: dict,
    options: MasqueradeImportOptions,
    system: System,
    db: AsyncSession,
) -> MasqueradeImportResult:
    """Import a parsed Masquerade export into the user's system."""
    result = MasqueradeImportResult()
    warnings: list[str] = []
    # Tally over-cap fields clamped during this run; surfaced as warnings at
    # the end (the preview shows the same prediction up front).
    report = ClampReport()

    profiles = list(enumerate(_profiles(data)))
    if options.member_ids is not None:
        wanted = set(options.member_ids)
        profiles = [(idx, p) for idx, p in profiles if str(idx) in wanted]

    # Build candidates first (no DB writes), so the member-cap check
    # below counts only the rows this run would actually CREATE.
    candidates: list[Member] = []
    profiles_no_name = 0
    for _idx, profile in profiles:
        member = _build_member(profile, system.id, report)
        if member is None:
            profiles_no_name += 1
            continue
        candidates.append(member)

    index = await load_member_match_index(db, system.id)
    new_count = count_new_members(
        [candidate_key(m) for m in candidates],
        index=index,
        strategy=options.conflict_strategy,
    )
    await enforce_import_member_cap(db, system, new_count)

    for member in candidates:
        resolution = await resolve_member(
            member,
            index=index,
            strategy=options.conflict_strategy,
            db=db,
            system=system,
        )
        if resolution.privacy_held_member_id:
            result.members_privacy_skipped += 1
            warnings.append(privacy_hold_warning(resolution.privacy_held_member_id))
        if resolution.disposition == "created":
            db.add(resolution.member)
            result.members_imported += 1
        elif resolution.disposition == "updated":
            result.members_updated += 1
        else:
            result.members_skipped += 1

    await db.flush()

    if profiles_no_name:
        warnings.append(
            f"Skipped {profiles_no_name} profile rows with no name (malformed "
            "export row)."
        )
    # Clamp tally goes last so a "3 member names were shortened" note follows
    # the per-record warnings in the job log.
    result.warnings = warnings + report.to_warnings()
    return result


def _build_member(
    profile: dict, system_id: uuid.UUID, report: ClampReport
) -> Member | None:
    """Construct a Sheaf Member from a Masquerade profile object.

    Returns None if the row lacks a usable name. Masquerade's `hidden`
    flag is not a privacy model (it hides the profile from the bot's
    listings), so every imported member defaults to PRIVATE - users can
    flip individual members to public after import if they want.
    """
    plaintext_name = _clean_str(profile.get("name"))
    if not plaintext_name:
        return None
    plaintext_name = clamp_str(plaintext_name, il.M_NAME, report=report)

    member_id = uuid.uuid4()
    return Member(
        id=member_id,
        system_id=system_id,
        name=encrypt(plaintext_name, aad=member_name_aad(member_id)),
        name_hash=blind_index(plaintext_name),
        display_name=clamp_str(
            _clean_str(profile.get("display_name")), il.M_DISPLAY_NAME, report=report
        ),
        # Foreign CDN URL; the sanitizer enforces http(s), drops refs to this
        # instance's own storage, and honours allow_external_images.
        avatar_url=sanitize_external_avatar_url(_clean_str(profile.get("avatar_url"))),
        color=_normalize_color(profile.get("color")),
        pronouns=None,  # Masquerade doesn't model pronouns.
        privacy=PrivacyLevel.PRIVATE,
    )

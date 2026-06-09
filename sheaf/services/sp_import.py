"""SimplyPlural data import service.

SP exports are JSON objects keyed by MongoDB collection name, each containing
an array of documents. The key collections we care about:

- members: system members/alters
- frontStatuses: custom front definitions (non-member fronting entities)
- frontHistory: front tracking records
- customFields: custom field definitions (values stored in member.info map)
- groups: member groups with parent hierarchy
- notes: per-member journal entries
- users: system profile (username, desc, color)
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import blind_index, encrypt
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue, FieldType
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.member import Member, front_members, group_members
from sheaf.models.system import System
from sheaf.schemas.sp_import import (
    SPImportOptions,
    SPImportResult,
    SPPreviewCustomFront,
    SPPreviewMember,
    SPPreviewSummary,
)
from sheaf.services.custom_fields import encrypt_field_value
from sheaf.services.member_limits import enforce_import_member_cap

logger = logging.getLogger("sheaf.import")

# SP custom field type → Sheaf field type
_SP_FIELD_TYPE_MAP: dict[int, FieldType] = {
    0: FieldType.TEXT,       # string
    1: FieldType.TEXT,       # color (hex string)
    2: FieldType.DATE,       # full date
    3: FieldType.TEXT,       # month only
    4: FieldType.TEXT,       # year only
    5: FieldType.TEXT,       # month + year
    6: FieldType.DATE,       # timestamp
    7: FieldType.TEXT,       # month + day
}


def _get_collection(data: dict, name: str) -> list[dict]:
    """Get a collection from SP export data, returning empty list if missing."""
    return data.get(name, [])


def _ms_to_datetime(ms: int | float | None) -> datetime | None:
    """Convert millisecond epoch timestamp to datetime."""
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def preview(data: dict) -> SPPreviewSummary:
    """Parse SP export JSON and return a summary for the user to review."""
    members = _get_collection(data, "members")
    custom_fronts = _get_collection(data, "frontStatuses")
    fronts = _get_collection(data, "frontHistory")
    groups = _get_collection(data, "groups")
    fields = _get_collection(data, "customFields")
    notes = _get_collection(data, "notes")

    # System profile from users collection
    users = _get_collection(data, "users")
    system_name = users[0].get("username") if users else None

    return SPPreviewSummary(
        system_name=system_name,
        member_count=len(members),
        members=[
            SPPreviewMember(id=m.get("_id", ""), name=m.get("name", "unnamed"))
            for m in members
        ],
        custom_front_count=len(custom_fronts),
        custom_fronts=[
            SPPreviewCustomFront(id=cf.get("_id", ""), name=cf.get("name", "unnamed"))
            for cf in custom_fronts
        ],
        front_history_count=len(fronts),
        group_count=len(groups),
        custom_field_count=len(fields),
        note_count=len(notes),
    )


async def run_import(
    data: dict,
    options: SPImportOptions,
    system: System,
    db: AsyncSession,
) -> SPImportResult:
    """Import SP export data into the user's system."""
    result = SPImportResult()
    warnings: list[str] = []

    # --- System profile ---
    if options.system_profile:
        users = _get_collection(data, "users")
        if users:
            sp_user = users[0]
            if sp_user.get("username") and not system.name:
                system.name = sp_user["username"][:100]
            if sp_user.get("desc"):
                system.description = sp_user["desc"]
            if sp_user.get("color"):
                system.color = _normalize_color(sp_user["color"])

    # --- Members ---
    sp_members = _get_collection(data, "members")
    # Filter to selected members if specified
    if options.member_ids is not None:
        selected = set(options.member_ids)
        sp_members = [m for m in sp_members if m.get("_id") in selected]

    # Custom fronts also become Member rows and count toward the cap, so fold
    # them into the headroom check. Hard-fail before writing anything.
    incoming = len(sp_members)
    if options.custom_fronts:
        incoming += len(_get_collection(data, "frontStatuses"))
    await enforce_import_member_cap(db, system, incoming)

    # Map SP member _id → new Sheaf Member for cross-referencing
    sp_id_to_member: dict[str, Member] = {}

    for sp_m in sp_members:
        sp_id = sp_m.get("_id", "")
        plaintext_name = (sp_m.get("name") or "unnamed")[:100]
        plaintext_description = sp_m.get("desc")
        member = Member(
            id=uuid.uuid4(),
            system_id=system.id,
            name=encrypt(plaintext_name),
            name_hash=blind_index(plaintext_name),
            display_name=_truncate(sp_m.get("displayName"), 100),
            description=(
                encrypt(plaintext_description)
                if plaintext_description is not None
                else None
            ),
            pronouns=_truncate(sp_m.get("pronouns"), 100),
            avatar_url=_truncate(sp_m.get("avatarUrl"), 500),
            color=_normalize_color(sp_m.get("color")),
            privacy=_map_privacy(sp_m.get("private", True)),
        )
        db.add(member)
        sp_id_to_member[sp_id] = member
        result.members_imported += 1

    # --- Custom fronts → imported as Members with is_custom_front=True ---
    # SP's "frontStatuses" are non-counting fronting entities like "Asleep"
    # or "Away". Sheaf models them as Members carrying the is_custom_front
    # flag, which the UI uses to list them separately from headcounted
    # members and exclude them from member-count statistics.
    sp_id_to_custom_front: dict[str, Member] = {}
    if options.custom_fronts:
        for sp_cf in _get_collection(data, "frontStatuses"):
            sp_id = sp_cf.get("_id", "")
            plaintext_cf_name = (sp_cf.get("name") or "unnamed")[:100]
            plaintext_cf_description = sp_cf.get("desc")
            member = Member(
                id=uuid.uuid4(),
                system_id=system.id,
                name=encrypt(plaintext_cf_name),
                name_hash=blind_index(plaintext_cf_name),
                description=(
                    encrypt(plaintext_cf_description)
                    if plaintext_cf_description is not None
                    else None
                ),
                color=_normalize_color(sp_cf.get("color")),
                avatar_url=_truncate(sp_cf.get("avatarUrl"), 500),
                privacy=_map_privacy(sp_cf.get("private", True)),
                is_custom_front=True,
            )
            db.add(member)
            sp_id_to_custom_front[sp_id] = member
            result.custom_fronts_imported += 1

    # Flush to get member IDs assigned
    await db.flush()

    # Combined lookup for front history references
    all_sp_to_member = {**sp_id_to_member, **sp_id_to_custom_front}

    # --- Custom fields ---
    sp_field_id_to_def: dict[str, CustomFieldDefinition] = {}
    if options.custom_fields:
        for idx, sp_f in enumerate(_get_collection(data, "customFields")):
            sp_fid = sp_f.get("_id", "")
            sp_type = sp_f.get("type", 0)
            sheaf_type = _SP_FIELD_TYPE_MAP.get(sp_type, FieldType.TEXT)

            field_def = CustomFieldDefinition(
                id=uuid.uuid4(),
                system_id=system.id,
                name=(sp_f.get("name") or f"field_{idx}")[:100],
                field_type=sheaf_type,
                order=idx,
            )
            db.add(field_def)
            sp_field_id_to_def[sp_fid] = field_def
            result.custom_fields_imported += 1

        await db.flush()

        # Now import field values from member info maps. Index sp_members
        # by _id once to avoid O(n*m) lookups on systems with thousands
        # of members.
        sp_member_by_id = {m.get("_id"): m for m in sp_members if m.get("_id")}
        unknown_field_refs = 0
        for sp_id, member in sp_id_to_member.items():
            sp_m = sp_member_by_id.get(sp_id)
            if not sp_m:
                continue
            info = sp_m.get("info", {})
            if not isinstance(info, dict):
                continue
            for field_sp_id, raw_value in info.items():
                if raw_value is None:
                    continue
                field_def = sp_field_id_to_def.get(field_sp_id)
                if not field_def:
                    unknown_field_refs += 1
                    continue
                cfv = CustomFieldValue(
                    id=uuid.uuid4(),
                    field_id=field_def.id,
                    member_id=member.id,
                    value=encrypt_field_value({"v": str(raw_value)}),
                )
                db.add(cfv)
        if unknown_field_refs:
            warnings.append(
                f"Dropped {unknown_field_refs} custom-field values whose "
                "field definition wasn't in the export (probably deleted "
                "in SimplyPlural after the values were set)."
            )

    # --- Groups ---
    if options.groups:
        sp_groups = _get_collection(data, "groups")
        sp_gid_to_group: dict[str, Group] = {}

        # First pass: create groups without parent links
        for sp_g in sp_groups:
            sp_gid = sp_g.get("_id", "")
            group = Group(
                id=uuid.uuid4(),
                system_id=system.id,
                name=(sp_g.get("name") or "unnamed")[:100],
                description=sp_g.get("desc"),
                color=_normalize_color(sp_g.get("color")),
            )
            db.add(group)
            sp_gid_to_group[sp_gid] = group
            result.groups_imported += 1

        await db.flush()

        # Second pass: wire parent links and member associations
        unknown_group_members = 0
        unresolvable_parents = 0
        for sp_g in sp_groups:
            sp_gid = sp_g.get("_id", "")
            group = sp_gid_to_group.get(sp_gid)
            if not group:
                continue

            # Parent
            sp_parent = sp_g.get("parent")
            if sp_parent and sp_parent != "root":
                parent_group = sp_gid_to_group.get(sp_parent)
                if parent_group is not None:
                    group.parent_id = parent_group.id
                else:
                    unresolvable_parents += 1

            # Members
            for sp_mid in sp_g.get("members", []):
                member = all_sp_to_member.get(sp_mid)
                if member is None:
                    unknown_group_members += 1
                    continue
                await db.execute(
                    group_members.insert().values(
                        group_id=group.id, member_id=member.id
                    )
                )
        if unknown_group_members:
            warnings.append(
                f"Dropped {unknown_group_members} group-membership references "
                "whose member wasn't selected for import."
            )
        if unresolvable_parents:
            warnings.append(
                f"Dropped {unresolvable_parents} group parent links that "
                "pointed at a group not present in the export."
            )

    # --- Front history ---
    if options.front_history:
        fronts_missing_member = 0
        fronts_missing_member_ref = 0
        for sp_f in _get_collection(data, "frontHistory"):
            sp_member_id = sp_f.get("member")
            if not sp_member_id:
                fronts_missing_member_ref += 1
                continue

            member = all_sp_to_member.get(sp_member_id)
            if not member:
                # Front references a member/custom front that wasn't imported
                fronts_missing_member += 1
                continue

            started = _ms_to_datetime(sp_f.get("startTime"))
            if not started:
                warnings.append(
                    f"Skipped front with no startTime: {sp_f.get('_id', '?')}"
                )
                continue

            ended = _ms_to_datetime(sp_f.get("endTime"))
            is_live = sp_f.get("live", False)
            if is_live:
                ended = None

            front = Front(
                id=uuid.uuid4(),
                system_id=system.id,
                started_at=started,
                ended_at=ended,
            )
            db.add(front)
            await db.flush()

            await db.execute(
                front_members.insert().values(
                    front_id=front.id, member_id=member.id
                )
            )
            result.fronts_imported += 1
        if fronts_missing_member:
            warnings.append(
                f"Skipped {fronts_missing_member} front-history rows that "
                "referenced a member not selected for import."
            )
        if fronts_missing_member_ref:
            warnings.append(
                f"Skipped {fronts_missing_member_ref} front-history rows "
                "with no member id (malformed export row)."
            )

    # --- Notes (skipped until journal feature) ---
    if options.notes:
        note_count = len(_get_collection(data, "notes"))
        if note_count:
            result.notes_skipped = note_count
            warnings.append(
                f"Skipped {note_count} notes — journal feature not yet implemented. "
                "Notes will be importable once journals ship."
            )

    result.warnings = warnings
    return result


def _normalize_color(color: str | None) -> str | None:
    """Normalize a color value to 7-char hex or None."""
    if not color:
        return None
    color = color.strip()
    if color.startswith("#") and len(color) == 7:
        return color
    if color.startswith("#") and len(color) == 4:
        # Expand shorthand #abc → #aabbcc
        return f"#{color[1]*2}{color[2]*2}{color[3]*2}"
    if not color.startswith("#") and len(color) == 6:
        return f"#{color}"
    return color[:7] if color.startswith("#") else None


def _map_privacy(private: bool) -> str:
    """Map SP's boolean privacy to our enum value."""
    from sheaf.models.system import PrivacyLevel
    return PrivacyLevel.PRIVATE if private else PrivacyLevel.PUBLIC


def _truncate(value: str | None, max_len: int) -> str | None:
    """Truncate a string or return None."""
    if not value:
        return None
    return value[:max_len]

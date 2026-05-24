"""Sheaf data import service.

Imports data from Sheaf's own export format. Versions "1" and "2" are
accepted; "2" added top-level keys over time (reminders, watch_tokens,
polls, journals, revisions, uploaded_files) which this importer
deliberately does not consume — those carry runtime state or live
deliverability info that doesn't round-trip into a fresh instance
without re-registration. The fields here are silently ignored when
present.

Generates new UUIDs for all imported entities and maps old IDs to new
ones for cross-references inside the file.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import blind_index, encrypt
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue, FieldType
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.member import Member, front_members, group_members, member_tags
from sheaf.models.system import PrivacyLevel, System
from sheaf.models.tag import Tag
from sheaf.services.custom_fields import encrypt_field_value

logger = logging.getLogger("sheaf.import.sheaf")


_VALID_PRIVACY = {e.value for e in PrivacyLevel}
_VALID_FIELD_TYPE = {e.value for e in FieldType}


def _privacy(val: str | None) -> str:
    if val and val in _VALID_PRIVACY:
        return val
    return PrivacyLevel.PRIVATE


def _field_type(val: str | None) -> FieldType:
    if val and val in _VALID_FIELD_TYPE:
        return FieldType(val)
    return FieldType.TEXT


class SheafPreviewSummary:
    def __init__(self):
        self.system_name: str | None = None
        self.member_count: int = 0
        self.members: list[dict] = []
        self.front_count: int = 0
        self.group_count: int = 0
        self.tag_count: int = 0
        self.custom_field_count: int = 0


class SheafImportResult:
    def __init__(self):
        self.members_imported: int = 0
        self.fronts_imported: int = 0
        self.groups_imported: int = 0
        self.tags_imported: int = 0
        self.custom_fields_imported: int = 0
        self.warnings: list[str] = []


def preview(data: dict) -> SheafPreviewSummary:
    """Parse Sheaf export JSON and return a summary for user review."""
    summary = SheafPreviewSummary()

    system = data.get("system")
    if system:
        summary.system_name = system.get("name")

    members = data.get("members", [])
    summary.member_count = len(members)
    summary.members = [
        {"id": m.get("id", ""), "name": m.get("name", "unnamed")}
        for m in members
    ]

    summary.front_count = len(data.get("fronts", []))
    summary.group_count = len(data.get("groups", []))
    summary.tag_count = len(data.get("tags", []))
    summary.custom_field_count = len(data.get("custom_fields", []))

    return summary


async def run_import(
    data: dict,
    system: System,
    db: AsyncSession,
    *,
    system_profile: bool = True,
    member_ids: list[str] | None = None,
    fronts: bool = True,
    groups: bool = True,
    tags: bool = True,
    custom_fields: bool = True,
) -> SheafImportResult:
    """Import Sheaf export data into the user's system."""
    result = SheafImportResult()
    warnings: list[str] = []

    # --- System profile ---
    if system_profile:
        sys_data = data.get("system")
        if sys_data:
            if sys_data.get("name"):
                system.name = sys_data["name"][:100]
            if sys_data.get("description") is not None:
                system.description = sys_data["description"]
            if sys_data.get("tag") is not None:
                system.tag = sys_data["tag"][:8] if sys_data["tag"] else None
            if sys_data.get("color") is not None:
                system.color = sys_data["color"][:7] if sys_data["color"] else None
            if sys_data.get("privacy"):
                system.privacy = _privacy(sys_data["privacy"])
            # Notes are encrypted at rest. Empty-string clears (matches the
            # PATCH /systems/me semantics).
            if "note" in sys_data:
                note_val = sys_data["note"]
                system.note = encrypt(note_val) if note_val else None

    # --- Members ---
    export_members = data.get("members", [])
    if member_ids is not None:
        selected = set(member_ids)
        export_members = [m for m in export_members if m.get("id") in selected]

    # Map old export ID → new Member
    old_id_to_member: dict[str, Member] = {}

    for m_data in export_members:
        old_id = m_data.get("id", "")
        plaintext_name = (m_data.get("name") or "unnamed")[:100]
        plaintext_description = m_data.get("description")
        plaintext_note = m_data.get("note")
        member = Member(
            id=uuid.uuid4(),
            system_id=system.id,
            name=encrypt(plaintext_name),
            name_hash=blind_index(plaintext_name),
            display_name=_trunc(m_data.get("display_name"), 100),
            description=(
                encrypt(plaintext_description)
                if plaintext_description is not None
                else None
            ),
            note=(
                encrypt(plaintext_note)
                if plaintext_note
                else None
            ),
            pronouns=_trunc(m_data.get("pronouns"), 100),
            avatar_url=_trunc(m_data.get("avatar_url"), 500),
            color=_trunc(m_data.get("color"), 7),
            birthday=_trunc(m_data.get("birthday"), 10),
            pluralkit_id=_trunc(m_data.get("pluralkit_id"), 8),
            emoji=_trunc(m_data.get("emoji"), 8),
            is_custom_front=bool(m_data.get("is_custom_front", False)),
            privacy=_privacy(m_data.get("privacy")),
            quick_switch_pin=_coerce_pin(m_data.get("quick_switch_pin")),
        )
        db.add(member)
        old_id_to_member[old_id] = member
        result.members_imported += 1

    await db.flush()

    # --- Tags ---
    old_tag_to_tag: dict[str, Tag] = {}
    if tags:
        for t_data in data.get("tags", []):
            old_tid = t_data.get("id", "")
            tag = Tag(
                id=uuid.uuid4(),
                system_id=system.id,
                name=(t_data.get("name") or "unnamed")[:50],
                color=_trunc(t_data.get("color"), 7),
            )
            db.add(tag)
            old_tag_to_tag[old_tid] = tag
            result.tags_imported += 1

        await db.flush()

        # Wire tag-member associations
        for t_data in data.get("tags", []):
            old_tid = t_data.get("id", "")
            tag = old_tag_to_tag.get(old_tid)
            if not tag:
                continue
            for old_mid in t_data.get("member_ids", []):
                member = old_id_to_member.get(old_mid)
                if member:
                    await db.execute(
                        member_tags.insert().values(tag_id=tag.id, member_id=member.id)
                    )

    # --- Custom fields ---
    old_field_to_def: dict[str, CustomFieldDefinition] = {}
    if custom_fields:
        for fd_data in data.get("custom_fields", []):
            old_fid = fd_data.get("id", "")
            field_def = CustomFieldDefinition(
                id=uuid.uuid4(),
                system_id=system.id,
                name=(fd_data.get("name") or "field")[:100],
                field_type=_field_type(fd_data.get("field_type")),
                options=fd_data.get("options"),
                order=fd_data.get("order", 0),
                privacy=_privacy(fd_data.get("privacy")),
            )
            db.add(field_def)
            old_field_to_def[old_fid] = field_def
            result.custom_fields_imported += 1

        await db.flush()

        # Import field values
        for fd_data in data.get("custom_fields", []):
            old_fid = fd_data.get("id", "")
            field_def = old_field_to_def.get(old_fid)
            if not field_def:
                continue
            for v_data in fd_data.get("values", []):
                old_mid = v_data.get("member_id", "")
                member = old_id_to_member.get(old_mid)
                if not member:
                    continue
                cfv = CustomFieldValue(
                    id=uuid.uuid4(),
                    field_id=field_def.id,
                    member_id=member.id,
                    value=encrypt_field_value(v_data.get("value")),
                )
                db.add(cfv)

    # --- Groups ---
    if groups:
        export_groups = data.get("groups", [])
        old_gid_to_group: dict[str, Group] = {}

        # First pass: create groups without parent links
        for g_data in export_groups:
            old_gid = g_data.get("id", "")
            group = Group(
                id=uuid.uuid4(),
                system_id=system.id,
                name=(g_data.get("name") or "unnamed")[:100],
                description=g_data.get("description"),
                color=_trunc(g_data.get("color"), 7),
            )
            db.add(group)
            old_gid_to_group[old_gid] = group
            result.groups_imported += 1

        await db.flush()

        # Second pass: parent links and member associations
        for g_data in export_groups:
            old_gid = g_data.get("id", "")
            group = old_gid_to_group.get(old_gid)
            if not group:
                continue

            old_parent = g_data.get("parent_id")
            if old_parent and old_parent in old_gid_to_group:
                group.parent_id = old_gid_to_group[old_parent].id

            for old_mid in g_data.get("member_ids", []):
                member = old_id_to_member.get(old_mid)
                if member:
                    await db.execute(
                        group_members.insert().values(
                            group_id=group.id, member_id=member.id
                        )
                    )

    # --- Fronts ---
    if fronts:
        for f_data in data.get("fronts", []):
            started_at = _parse_iso(f_data.get("started_at"))
            if not started_at:
                warnings.append(f"Skipped front with invalid started_at: {f_data.get('id', '?')}")
                continue

            ended_at = _parse_iso(f_data.get("ended_at"))

            # Check that at least one member was imported
            front_member_ids = [
                old_id_to_member[mid].id
                for mid in f_data.get("member_ids", [])
                if mid in old_id_to_member
            ]
            if not front_member_ids:
                continue

            plaintext_status = f_data.get("custom_status")
            front = Front(
                id=uuid.uuid4(),
                system_id=system.id,
                started_at=started_at,
                ended_at=ended_at,
                custom_status=(
                    encrypt(plaintext_status)
                    if isinstance(plaintext_status, str) and plaintext_status
                    else None
                ),
            )
            db.add(front)
            await db.flush()

            for member_id in front_member_ids:
                await db.execute(
                    front_members.insert().values(
                        front_id=front.id, member_id=member_id
                    )
                )
            result.fronts_imported += 1

    result.warnings = warnings
    return result


def _trunc(val: str | None, max_len: int) -> str | None:
    if not val:
        return None
    return val[:max_len]


def _coerce_pin(val: object) -> int | None:
    """Quick-switch pin from import data: a non-negative int, else None.
    Guards against bools (bool is an int subclass) and junk values."""
    if isinstance(val, bool) or not isinstance(val, int):
        return None
    return val if val >= 0 else None


def _parse_iso(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None

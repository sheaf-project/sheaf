"""PluralSpace import.

PluralSpace exports a GDPR-style ZIP containing:
- `manifest.json` (export_date, system_name, format_version,
  user_email, regulation_reference)
- `data.json` (system + members + fronts + journal_entries +
  chat_channels + polls + thoughts + member_groups + custom_fields
  + media_files)
- `media/` (avatar blobs referenced by the JSON via
  `members[].avatar_media_path`)

The schema is reverse-engineered from a real format_version "1.1"
export. The importer is tolerant of missing optional fields and
warns rather than failing for shapes it doesn't recognise.

Mapping decisions worth flagging:
- `members[].role[]` -> Sheaf tags (one tag per distinct role).
  PluralSpace lets a single member carry multiple roles; Sheaf has
  no built-in role concept and tags are the closest match. Toggle
  with `roles_as_tags`.
- `member_groups[]` -> Sheaf groups. Flat in both systems.
- `fronts[]`: one PluralSpace row carries one member, `comment`,
  `started_at`, `ended_at`. Maps to one Sheaf Front row with one
  member and `comment` as `custom_status`. Co-fronts in PluralSpace
  are independent rows with overlapping time windows; we preserve
  that shape rather than merging.
- `journal_entries[]` are system-scoped on PluralSpace and can carry
  multiple member references as "participants". Sheaf journals are
  either system-scoped (no member_id) or per-member (one member_id).
  - Exactly one member -> per-member journal.
  - Zero or 2+ members -> system journal; we list the participating
    member names in the journal `author_member_names` for surface
    attribution.
- `visibility_level` (int) is dropped with a warning event because
  Sheaf has no journal visibility tier.
- `chat_channels[].messages[]` -> Sheaf system-board messages. Sheaf
  has system board + per-member walls, but not a free-form channel
  set. Each message lands on the system board with the channel name
  as a prefix line when more than one channel is present. One
  warning event per channel explains the collapse.
- `polls[]` map cleanly. `closes_at: null` becomes "1 year from
  creation" since Sheaf polls require a close time.
- `thoughts[]` are skipped entirely with a warning event; we don't
  have a thoughts-feature equivalent yet.

Avatar handling: media bytes from the zip are run through the
shared normalize_image pipeline (EXIF strip + dim cap + re-encode +
optional animation gate) and stored as the importing user's
UploadedFile rows. Quota is checked before each put so a large
export can't overrun a tight-tier user.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.config import settings
from sheaf.crypto import blind_index, encrypt
from sheaf.image_processing import ImageNormalizationError, normalize_image
from sheaf.models.custom_field import (
    CustomFieldDefinition,
    CustomFieldValue,
    FieldType,
)
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.journal_entry import JournalEntry
from sheaf.models.member import (
    Member,
    front_members,
    group_members,
    member_tags,
)
from sheaf.models.message import BoardKind, Message
from sheaf.models.poll import (
    Poll,
    PollKind,
    PollOption,
    PollResultsVisibility,
    PollVote,
)
from sheaf.models.system import PrivacyLevel, System
from sheaf.models.tag import Tag
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User, UserTier
from sheaf.schemas.pluralspace_import import (
    PluralspaceImportResult,
    PluralspacePreviewMember,
    PluralspacePreviewSummary,
)
from sheaf.services.custom_fields import encrypt_field_value
from sheaf.services.import_parsing import (
    ImportPayloadError,
    safe_json_loads,
    sanitize_external_avatar_url,
)
from sheaf.services.member_limits import enforce_import_member_cap
from sheaf.storage import get_storage

logger = logging.getLogger("sheaf.imports.pluralspace")


# --- Zip + JSON parsing ----------------------------------------------------

# Image upload pipeline mirrors files.py. Kept private here so the
# importer doesn't need to depend on the v1 router module.
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _sniff_image_mime(data: bytes) -> str | None:
    """Magic-byte image format sniffer. Same predicates as the upload endpoint."""
    if len(data) < 12:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass
class _ParsedExport:
    """The parsed contents of a PluralSpace export zip.

    Holds the data dict, manifest dict, and a lazily-accessed media
    blob registry. The zip handle is kept open as long as this struct
    exists so callers can fetch media bytes by path.
    """

    manifest: dict
    data: dict
    zf: zipfile.ZipFile
    media_paths: set[str] = field(default_factory=set)

    def read_media(self, path: str) -> bytes | None:
        """Return the bytes for a media file inside the zip, or None
        if the path isn't a known entry (avoids ZipFile raising)."""
        if path not in self.media_paths:
            return None
        try:
            if self.zf.getinfo(path).file_size > _MAX_MEDIA_DECOMPRESSED:
                return None
            with self.zf.open(path) as fh:
                return fh.read()
        except KeyError:
            return None


# Decompressed-size caps. DEFLATE reaches roughly 1000:1, so a 100MB
# upload could otherwise expand to ~100GB in memory when read. The JSON
# cap matches the Prism importer's plaintext-JSON cap; the media cap
# matches the upload size cap. Python's zipfile additionally refuses a
# stream that overruns its declared size, so the declared sizes checked
# here are what reads actually enforce.
_MAX_JSON_DECOMPRESSED = 256 * 1024 * 1024
_MAX_MANIFEST_DECOMPRESSED = 4 * 1024 * 1024
_MAX_MEDIA_DECOMPRESSED = 100 * 1024 * 1024


def parse_export(blob: bytes) -> _ParsedExport:
    """Open the zip and validate it has the expected manifest + data shape.

    Raises ImportPayloadError on anything that's not recognisable as
    a PluralSpace export — the user-facing message names the cause.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ImportPayloadError("file is not a valid zip archive") from exc

    names = set(zf.namelist())
    if "manifest.json" not in names or "data.json" not in names:
        raise ImportPayloadError(
            "PluralSpace export must contain manifest.json and data.json"
        )

    for name, cap in (
        ("manifest.json", _MAX_MANIFEST_DECOMPRESSED),
        ("data.json", _MAX_JSON_DECOMPRESSED),
    ):
        if zf.getinfo(name).file_size > cap:
            raise ImportPayloadError(
                f"{name} decompresses to more than "
                f"{cap // (1024 * 1024)}MB; refusing to parse"
            )

    try:
        manifest_raw = zf.read("manifest.json")
        data_raw = zf.read("data.json")
    except KeyError as exc:
        raise ImportPayloadError(f"could not read entry: {exc}") from exc

    # safe_json_loads caps the parsed element count (same guard the
    # JSON-file importers use) so a small-but-dense payload can't DoS
    # the walk even inside the decompressed-size cap.
    try:
        manifest = safe_json_loads(manifest_raw)
        data = safe_json_loads(data_raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ImportPayloadError(f"invalid JSON inside export: {exc}") from exc

    if not isinstance(manifest, dict):
        raise ImportPayloadError("manifest.json must be a JSON object")
    if not isinstance(data, dict):
        raise ImportPayloadError("data.json must be a JSON object")

    media_paths = {n for n in names if n.startswith("media/") and not n.endswith("/")}
    return _ParsedExport(manifest=manifest, data=data, zf=zf, media_paths=media_paths)


# A worst-case parse decompresses and JSON-loads up to
# _MAX_JSON_DECOMPRESSED bytes in one go: seconds of CPU and a large
# allocation, far too heavy for the event loop (the import runner loop
# shares it with live request handling). Parses run in a worker thread,
# a couple at a time so concurrent imports can't stack the allocations.
_parse_semaphore: asyncio.Semaphore | None = None


def _get_parse_semaphore() -> asyncio.Semaphore:
    global _parse_semaphore
    if _parse_semaphore is None:
        _parse_semaphore = asyncio.Semaphore(2)
    return _parse_semaphore


async def parse_export_async(blob: bytes) -> _ParsedExport:
    async with _get_parse_semaphore():
        return await asyncio.to_thread(parse_export, blob)


# --- Preview ---------------------------------------------------------------


def preview(parsed: _ParsedExport) -> PluralspacePreviewSummary:
    """Walk the parsed export and return a counts + member-list summary.

    Doesn't write anything. The export_date / format_version are surfaced
    for the UI to display alongside the counts.
    """
    manifest = parsed.manifest
    data = parsed.data

    members_raw = _list(data.get("members"))
    member_summaries: list[PluralspacePreviewMember] = []
    custom_front_count = 0
    for m in members_raw:
        if not isinstance(m, dict):
            continue
        is_cf = bool(m.get("is_custom_front"))
        if is_cf:
            custom_front_count += 1
        member_summaries.append(
            PluralspacePreviewMember(
                id=str(m.get("id") or "")[:64],
                name=_clean_str(m.get("name")) or "unnamed",
                is_custom_front=is_cf,
                is_archived=bool(m.get("is_archived")),
                has_avatar=bool(
                    _clean_str(m.get("avatar_media_path"))
                    or _clean_str(m.get("avatar_path"))
                ),
                roles=[r for r in _list(m.get("role")) if isinstance(r, str)],
                groups=[g for g in _list(m.get("groups")) if isinstance(g, str)],
            )
        )

    chat_channels = _list(data.get("chat_channels"))
    chat_msg_count = sum(
        len(_list(c.get("messages"))) for c in chat_channels if isinstance(c, dict)
    )

    sys_block = data.get("system") if isinstance(data.get("system"), dict) else {}
    sys_name = _clean_str(sys_block.get("name")) or _clean_str(manifest.get("system_name"))

    return PluralspacePreviewSummary(
        system_name=sys_name,
        format_version=_clean_str(manifest.get("format_version")),
        export_date=_parse_iso(manifest.get("export_date")),
        member_count=len(member_summaries),
        custom_front_count=custom_front_count,
        members=member_summaries,
        group_count=len(_list(data.get("member_groups"))),
        custom_field_count=len(_list(data.get("custom_fields"))),
        front_count=len(_list(data.get("fronts"))),
        journal_entry_count=len(_list(data.get("journal_entries"))),
        chat_channel_count=len(chat_channels),
        chat_message_count=chat_msg_count,
        poll_count=len(_list(data.get("polls"))),
        thought_count=len(_list(data.get("thoughts"))),
        media_file_count=len(parsed.media_paths),
    )


# --- Import ----------------------------------------------------------------


async def run_import(
    parsed: _ParsedExport,
    system: System,
    user: User,
    db: AsyncSession,
    *,
    system_profile: bool = True,
    member_ids: list[str] | None = None,
    custom_fronts: bool = True,
    member_avatars: bool = True,
    roles_as_tags: bool = True,
    groups: bool = True,
    custom_fields: bool = True,
    fronts: bool = True,
    journal_entries: bool = True,
    chat_messages: bool = True,
    polls: bool = True,
) -> PluralspaceImportResult:
    """Drive a PluralSpace import into the importing user's system.

    Returns a result with per-section counts and a `warnings` list of
    user-facing messages (one per surfaced issue). The runner converts
    each warning to a `level=warning, stage=import` event.
    """
    result = PluralspaceImportResult()
    data = parsed.data

    # --- System profile ----
    if system_profile:
        _apply_system_profile(data, system)

    # --- Members (regular + custom fronts) ----
    selected = set(member_ids) if member_ids is not None else None
    members_in = _list(data.get("members"))

    # Pre-filter to the rows that will actually become Member records so
    # the tier member-cap check counts exactly what the loop below would
    # write. Hard-fails (clean job error) before anything is written.
    eligible: list[dict] = []
    for m_data in members_in:
        if not isinstance(m_data, dict):
            continue
        ps_id = _clean_str(m_data.get("id"))
        if not ps_id:
            result.warnings.append("Skipped a member with no id.")
            continue
        if selected is not None and ps_id not in selected:
            continue
        if bool(m_data.get("is_custom_front")) and not custom_fronts:
            continue
        eligible.append(m_data)
    await enforce_import_member_cap(db, system, len(eligible))

    ps_id_to_member: dict[str, Member] = {}
    member_name_to_member: dict[str, Member] = {}

    for m_data in eligible:
        ps_id = _clean_str(m_data.get("id"))
        is_cf = bool(m_data.get("is_custom_front"))

        plaintext_name = (_clean_str(m_data.get("name")) or "unnamed")[:100]
        plaintext_description = _clean_str(m_data.get("description"))
        member = Member(
            id=uuid.uuid4(),
            system_id=system.id,
            name=encrypt(plaintext_name),
            name_hash=blind_index(plaintext_name),
            display_name=_truncate(_clean_str(m_data.get("display_name")), 100),
            description=(
                encrypt(plaintext_description) if plaintext_description else None
            ),
            pronouns=_truncate(_clean_str(m_data.get("pronouns")), 100),
            color=_normalize_color(m_data.get("color")),
            is_custom_front=is_cf,
            privacy=PrivacyLevel.PRIVATE,
        )
        db.add(member)
        ps_id_to_member[ps_id] = member
        member_name_to_member[plaintext_name] = member
        if is_cf:
            result.custom_fronts_imported += 1
        else:
            result.members_imported += 1

    if not ps_id_to_member:
        # Nothing else has anywhere to live; bail before walking the rest.
        return result

    await db.flush()

    # --- Avatars ----
    if member_avatars:
        external_avatars_skipped = 0
        for m_data in members_in:
            if not isinstance(m_data, dict):
                continue
            ps_id = _clean_str(m_data.get("id"))
            member = ps_id_to_member.get(ps_id) if ps_id else None
            if member is None:
                continue

            media_path = _clean_str(m_data.get("avatar_media_path"))
            url_path = _clean_str(m_data.get("avatar_path"))

            if media_path:
                stored_key = await _persist_avatar_from_zip(
                    parsed, media_path, db, user, result.warnings
                )
                if stored_key:
                    member.avatar_url = stored_key[:500]
                    result.avatars_imported += 1
            elif url_path:
                # PluralSpace can also carry external avatar URLs in
                # `avatar_path`; pass those through (rather than
                # re-hosting) when the scheme is plain http(s) and the
                # instance allows external images.
                safe_url = sanitize_external_avatar_url(url_path)
                if safe_url:
                    member.avatar_url = safe_url
                else:
                    external_avatars_skipped += 1
        if external_avatars_skipped:
            result.warnings.append(
                f"Skipped {external_avatars_skipped} external avatar "
                "link(s): external images are not allowed on this server, "
                "or the link was not a plain http(s) URL."
            )

    # --- Roles -> tags ----
    if roles_as_tags:
        result.tags_imported += await _import_roles_as_tags(
            members_in, ps_id_to_member, system.id, db
        )

    # --- Groups ----
    if groups:
        result.groups_imported += await _import_groups(
            _list(data.get("member_groups")),
            members_in,
            ps_id_to_member,
            system.id,
            db,
            result.warnings,
        )

    # --- Custom fields ----
    if custom_fields:
        result.custom_fields_imported += await _import_custom_fields(
            _list(data.get("custom_fields")),
            members_in,
            ps_id_to_member,
            system.id,
            db,
            result.warnings,
        )

    # --- Fronts ----
    if fronts:
        result.fronts_imported += await _import_fronts(
            _list(data.get("fronts")),
            ps_id_to_member,
            system.id,
            db,
            result.warnings,
        )

    # --- Journal entries ----
    if journal_entries:
        result.journals_imported += await _import_journals(
            _list(data.get("journal_entries")),
            ps_id_to_member,
            system,
            db,
            result.warnings,
        )

    # --- Chat channels -> board messages ----
    if chat_messages:
        result.messages_imported += await _import_chat(
            _list(data.get("chat_channels")),
            member_name_to_member,
            system.id,
            db,
            result.warnings,
        )

    # --- Polls ----
    if polls:
        result.polls_imported += await _import_polls(
            _list(data.get("polls")),
            ps_id_to_member,
            member_name_to_member,
            system.id,
            db,
            result.warnings,
        )

    # --- Thoughts (unsupported) ----
    thought_count = len(_list(data.get("thoughts")))
    if thought_count:
        result.warnings.append(
            f"Skipped {thought_count} thought entries: Sheaf doesn't have a "
            "thoughts-feature equivalent yet."
        )

    return result


# --- Section helpers -------------------------------------------------------


def _apply_system_profile(data: dict, system: System) -> None:
    """Copy non-destructive system fields onto Sheaf's system.

    Only fills empty Sheaf fields. Never overwrites a user-set name.
    """
    sys_data = data.get("system")
    if not isinstance(sys_data, dict):
        return
    name = _clean_str(sys_data.get("name"))
    if name and not system.name:
        system.name = name[:100]
    description = _clean_str(sys_data.get("description"))
    if description and not system.description:
        system.description = description
    color = _normalize_color(sys_data.get("color"))
    if color and not system.color:
        system.color = color


async def _import_roles_as_tags(
    members_in: list,
    ps_id_to_member: dict[str, Member],
    system_id: uuid.UUID,
    db: AsyncSession,
) -> int:
    """Create one Tag per distinct PluralSpace role and wire memberships.

    Tags are deduped against existing system tags by name (case-insensitive)
    so a re-import on the same system doesn't duplicate the tag set.
    """
    existing = await db.execute(
        select(Tag).where(Tag.system_id == system_id)
    )
    tag_by_name: dict[str, Tag] = {
        t.name.lower(): t for t in existing.scalars().all()
    }

    created = 0
    for m_data in members_in:
        if not isinstance(m_data, dict):
            continue
        ps_id = _clean_str(m_data.get("id"))
        member = ps_id_to_member.get(ps_id) if ps_id else None
        if member is None:
            continue
        for role in _list(m_data.get("role")):
            role_str = _clean_str(role)
            if not role_str:
                continue
            key = role_str.lower()
            tag = tag_by_name.get(key)
            if tag is None:
                tag = Tag(
                    id=uuid.uuid4(),
                    system_id=system_id,
                    name=role_str[:50],
                )
                db.add(tag)
                tag_by_name[key] = tag
                created += 1
        await db.flush()
        for role in _list(m_data.get("role")):
            role_str = _clean_str(role)
            if not role_str:
                continue
            tag = tag_by_name.get(role_str.lower())
            if tag is None:
                continue
            # member_tags has a UNIQUE constraint; guard against re-association.
            existing_assoc = await db.execute(
                select(member_tags.c.tag_id).where(
                    member_tags.c.tag_id == tag.id,
                    member_tags.c.member_id == member.id,
                )
            )
            if existing_assoc.first() is not None:
                continue
            await db.execute(
                member_tags.insert().values(tag_id=tag.id, member_id=member.id)
            )
    return created


async def _import_groups(
    groups_in: list,
    members_in: list,
    ps_id_to_member: dict[str, Member],
    system_id: uuid.UUID,
    db: AsyncSession,
    warnings: list[str],
) -> int:
    """Create Sheaf Groups from PluralSpace member_groups + members[].groups[].

    PluralSpace stores group membership in two places: an inline
    `groups` array of names on each member, and a full
    `member_groups` block with id/name/color/description/members. We
    use member_groups[] as the source of truth for definitions and
    members[].groups[] as a fallback when a name appears on a member
    but no definition row exists for it (older exports).
    """
    name_to_group: dict[str, Group] = {}
    existing = await db.execute(
        select(Group).where(Group.system_id == system_id)
    )
    for g in existing.scalars().all():
        name_to_group[g.name.lower()] = g

    created = 0
    name_to_member_ids: dict[str, set[uuid.UUID]] = {}
    for g_data in groups_in:
        if not isinstance(g_data, dict):
            continue
        name = _clean_str(g_data.get("name"))
        if not name:
            continue
        key = name.lower()
        group = name_to_group.get(key)
        if group is None:
            group = Group(
                id=uuid.uuid4(),
                system_id=system_id,
                name=name[:100],
                description=_clean_str(g_data.get("description")),
                color=_normalize_color(g_data.get("color")),
            )
            db.add(group)
            name_to_group[key] = group
            created += 1
        member_refs = name_to_member_ids.setdefault(key, set())
        for m_ref in _list(g_data.get("members")):
            if not isinstance(m_ref, dict):
                continue
            ps_id = _clean_str(m_ref.get("id"))
            member = ps_id_to_member.get(ps_id) if ps_id else None
            if member is not None:
                member_refs.add(member.id)

    # Pick up groups that only appear on the per-member `groups` list.
    for m_data in members_in:
        if not isinstance(m_data, dict):
            continue
        ps_id = _clean_str(m_data.get("id"))
        member = ps_id_to_member.get(ps_id) if ps_id else None
        if member is None:
            continue
        for raw_name in _list(m_data.get("groups")):
            name = _clean_str(raw_name)
            if not name:
                continue
            key = name.lower()
            group = name_to_group.get(key)
            if group is None:
                group = Group(
                    id=uuid.uuid4(),
                    system_id=system_id,
                    name=name[:100],
                )
                db.add(group)
                name_to_group[key] = group
                created += 1
            name_to_member_ids.setdefault(key, set()).add(member.id)

    await db.flush()

    for key, member_ids in name_to_member_ids.items():
        group = name_to_group.get(key)
        if group is None:
            continue
        for member_id in member_ids:
            existing_assoc = await db.execute(
                select(group_members.c.member_id).where(
                    group_members.c.group_id == group.id,
                    group_members.c.member_id == member_id,
                )
            )
            if existing_assoc.first() is not None:
                continue
            await db.execute(
                group_members.insert().values(
                    group_id=group.id, member_id=member_id
                )
            )

    _ = warnings  # nothing to surface from groups today; reserved for future shapes
    return created


def _map_field_type(raw: Any) -> FieldType:
    """Coerce a PluralSpace field_type string to a Sheaf FieldType.

    Unknown / missing types default to TEXT (the most permissive).
    PluralSpace's date is mapped 1:1; other types fall through.
    """
    if not isinstance(raw, str):
        return FieldType.TEXT
    raw_l = raw.strip().lower()
    if raw_l in ("text",):
        return FieldType.TEXT
    if raw_l in ("number", "integer", "int"):
        return FieldType.NUMBER
    if raw_l in ("date",):
        return FieldType.DATE
    if raw_l in ("boolean", "bool"):
        return FieldType.BOOLEAN
    return FieldType.TEXT


async def _import_custom_fields(
    fields_in: list,
    members_in: list,
    ps_id_to_member: dict[str, Member],
    system_id: uuid.UUID,
    db: AsyncSession,
    warnings: list[str],
) -> int:
    """Create Sheaf custom-field definitions + per-member values.

    Dedup against existing definitions on (name, type) so a re-import
    doesn't duplicate the field list. Multi-value PluralSpace fields
    collapse to a newline-joined TEXT value because Sheaf's storage is
    one-value-per-(field, member).
    """
    existing = await db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.system_id == system_id
        )
    )
    by_key: dict[tuple[str, str], CustomFieldDefinition] = {
        (fd.name.lower(), fd.field_type.value): fd
        for fd in existing.scalars().all()
    }

    created = 0
    name_to_def: dict[str, CustomFieldDefinition] = {}
    multi_field_names: set[str] = set()

    for f_data in fields_in:
        if not isinstance(f_data, dict):
            continue
        name = _clean_str(f_data.get("name"))
        if not name:
            continue
        ftype = _map_field_type(f_data.get("field_type"))
        if bool(f_data.get("is_multiple")):
            multi_field_names.add(name.lower())
        key = (name.lower(), ftype.value)
        field_def = by_key.get(key)
        if field_def is None:
            field_def = CustomFieldDefinition(
                id=uuid.uuid4(),
                system_id=system_id,
                name=name[:100],
                field_type=ftype,
                privacy=PrivacyLevel.PRIVATE,
            )
            db.add(field_def)
            by_key[key] = field_def
            created += 1
        name_to_def[name.lower()] = field_def

    await db.flush()

    # Collect per-(member, field) values, joining multi-value entries.
    flat_collapsed = False
    pairs: dict[tuple[uuid.UUID, uuid.UUID], list[str]] = {}
    for m_data in members_in:
        if not isinstance(m_data, dict):
            continue
        ps_id = _clean_str(m_data.get("id"))
        member = ps_id_to_member.get(ps_id) if ps_id else None
        if member is None:
            continue
        for v in _list(m_data.get("custom_field_values")):
            if not isinstance(v, dict):
                continue
            fname = _clean_str(v.get("field_name"))
            value = v.get("value")
            if not fname or value in (None, ""):
                continue
            field_def = name_to_def.get(fname.lower())
            if field_def is None:
                continue
            pair_key = (field_def.id, member.id)
            values = pairs.setdefault(pair_key, [])
            values.append(str(value))
            if len(values) > 1 and fname.lower() in multi_field_names:
                flat_collapsed = True

    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for (field_id, member_id), values in pairs.items():
        if (field_id, member_id) in seen:
            continue
        seen.add((field_id, member_id))
        joined = "\n".join(values)
        cfv = CustomFieldValue(
            id=uuid.uuid4(),
            field_id=field_id,
            member_id=member_id,
            value=encrypt_field_value(joined),
        )
        db.add(cfv)

    if flat_collapsed:
        warnings.append(
            "PluralSpace multi-value custom fields were joined with newlines "
            "into a single Sheaf text value: Sheaf stores one value per "
            "(field, member)."
        )
    return created


async def _import_fronts(
    fronts_in: list,
    ps_id_to_member: dict[str, Member],
    system_id: uuid.UUID,
    db: AsyncSession,
    warnings: list[str],
) -> int:
    """Convert PluralSpace fronts to Sheaf Front rows.

    Each PluralSpace row -> one Sheaf Front with one member. The
    `comment` field maps to `custom_status` (encrypted). Front types
    other than "front" are kept as-is via a warning event.
    """
    imported = 0
    unknown_types: set[str] = set()
    missing_members = 0
    for f_data in fronts_in:
        if not isinstance(f_data, dict):
            continue
        started_at = _parse_iso(f_data.get("started_at"))
        if started_at is None:
            warnings.append(
                f"Skipped front {f_data.get('id') or '<no-id>'}: invalid started_at."
            )
            continue
        ended_at = _parse_iso(f_data.get("ended_at"))
        ps_member_id = _clean_str(f_data.get("member_id"))
        member = ps_id_to_member.get(ps_member_id) if ps_member_id else None
        if member is None:
            missing_members += 1
            continue
        ftype = _clean_str(f_data.get("type"))
        if ftype and ftype != "front":
            unknown_types.add(ftype)
        comment = _clean_str(f_data.get("comment"))
        front = Front(
            id=uuid.uuid4(),
            system_id=system_id,
            started_at=started_at,
            ended_at=ended_at,
            custom_status=encrypt(comment) if comment else None,
        )
        db.add(front)
        await db.flush()
        await db.execute(
            front_members.insert().values(front_id=front.id, member_id=member.id)
        )
        imported += 1

    if missing_members:
        warnings.append(
            f"Skipped {missing_members} front entries that referenced members "
            "not selected for import."
        )
    if unknown_types:
        warnings.append(
            "Front entry types other than 'front' were preserved as basic "
            f"fronts (saw: {', '.join(sorted(unknown_types))}). PluralSpace's "
            "type taxonomy doesn't fully map to Sheaf today."
        )
    return imported


async def _import_journals(
    entries_in: list,
    ps_id_to_member: dict[str, Member],
    system: System,
    db: AsyncSession,
    warnings: list[str],
) -> int:
    """Map PluralSpace journal entries to Sheaf JournalEntry rows.

    Multi-member entries land on the system board (no member_id) with
    `author_member_names` populated for surface attribution. Single
    member -> per-member entry. `visibility_level` is dropped because
    Sheaf has no visibility tier.
    """
    imported = 0
    dropped_visibility = 0
    for j_data in entries_in:
        if not isinstance(j_data, dict):
            continue
        title = _clean_str(j_data.get("title"))
        body = _clean_str(j_data.get("content")) or ""
        # PluralSpace inlines `{id, name}` for each referenced member;
        # the name is plaintext, so we can use it directly for author
        # attribution without decrypting the Sheaf row.
        resolved: list[tuple[Member, str]] = []
        for m_ref in _list(j_data.get("members")):
            if not isinstance(m_ref, dict):
                continue
            ps_id = _clean_str(m_ref.get("id"))
            sheaf_member = ps_id_to_member.get(ps_id) if ps_id else None
            if sheaf_member is None:
                continue
            name = _clean_str(m_ref.get("name")) or ""
            resolved.append((sheaf_member, name))
        if "visibility_level" in j_data:
            dropped_visibility += 1

        per_member_id: uuid.UUID | None = None
        author_names: list[str] = []
        author_ids: list[str] = []
        if len(resolved) == 1:
            per_member_id = resolved[0][0].id
        else:
            for sheaf_member, name in resolved:
                author_ids.append(str(sheaf_member.id))
                if name:
                    author_names.append(name)

        entry = JournalEntry(
            id=uuid.uuid4(),
            system_id=system.id,
            member_id=per_member_id,
            title=encrypt(title) if title else None,
            body=encrypt(body),
            visibility="system",
            author_user_id=system.user_id,
            author_member_ids=author_ids,
            author_member_names=author_names,
            image_keys=[],
        )
        created = _parse_iso(j_data.get("created_at"))
        if created:
            entry.created_at = created
        updated = _parse_iso(j_data.get("updated_at"))
        if updated:
            entry.updated_at = updated
        db.add(entry)
        imported += 1

    if dropped_visibility:
        warnings.append(
            f"Dropped visibility_level on {dropped_visibility} journal entries: "
            "Sheaf journals don't have visibility tiers."
        )
    return imported


async def _import_chat(
    channels_in: list,
    member_name_to_member: dict[str, Member],
    system_id: uuid.UUID,
    db: AsyncSession,
    warnings: list[str],
) -> int:
    """Collapse PluralSpace chat channels to Sheaf board messages.

    Every channel's messages land on the system board. When more than
    one channel is present, each message body is prefixed with
    `[<channel name>]` so the origin is preserved.
    """
    channels_in = [c for c in channels_in if isinstance(c, dict)]
    if not channels_in:
        return 0
    multi_channel = len(channels_in) > 1
    imported = 0
    if multi_channel:
        warnings.append(
            f"PluralSpace exported {len(channels_in)} chat channels. Sheaf "
            "has one system board; messages were merged with each body "
            "prefixed by its channel name."
        )
    missing_authors = 0
    for channel in channels_in:
        channel_name = _clean_str(channel.get("name")) or "channel"
        for msg in _list(channel.get("messages")):
            if not isinstance(msg, dict):
                continue
            body = _clean_str(msg.get("content")) or ""
            if multi_channel:
                body = f"[{channel_name}] {body}".rstrip()
            author_name = _clean_str(msg.get("member_name"))
            author = member_name_to_member.get(author_name) if author_name else None
            if author_name and author is None:
                missing_authors += 1
            message = Message(
                id=uuid.uuid4(),
                system_id=system_id,
                board_kind=BoardKind.SYSTEM.value,
                board_member_id=None,
                author_member_id=author.id if author else None,
                body=encrypt(body),
            )
            created = _parse_iso(msg.get("created_at"))
            if created:
                message.created_at = created
            db.add(message)
            imported += 1
    if missing_authors:
        warnings.append(
            f"{missing_authors} chat messages referenced an author member that "
            "wasn't imported; those messages were attributed to nobody."
        )
    return imported


async def _import_polls(
    polls_in: list,
    ps_id_to_member: dict[str, Member],
    member_name_to_member: dict[str, Member],
    system_id: uuid.UUID,
    db: AsyncSession,
    warnings: list[str],
) -> int:
    """Create Sheaf Polls from PluralSpace polls.

    PluralSpace polls store votes as `options[].votes[]`; Sheaf stores
    one PollVote row per (member, set of option ids). We invert the
    PluralSpace structure to build the Sheaf rows.

    Open-ended polls (`closes_at: null`) get a one-year-from-creation
    close time because Sheaf requires it.
    """
    imported = 0
    open_ended_count = 0
    missing_voter_count = 0
    for p_data in polls_in:
        if not isinstance(p_data, dict):
            continue
        question = _clean_str(p_data.get("title")) or ""
        description = _clean_str(p_data.get("description"))
        allow_multi = bool(p_data.get("allows_multiple_votes"))
        created_at = _parse_iso(p_data.get("created_at"))
        closes_at = _parse_iso(p_data.get("closes_at"))
        if closes_at is None:
            base = created_at or datetime.now(UTC)
            closes_at = base + timedelta(days=365)
            open_ended_count += 1

        poll = Poll(
            id=uuid.uuid4(),
            system_id=system_id,
            question=encrypt(question),
            description=encrypt(description) if description else None,
            kind=(
                PollKind.MULTI_CHOICE.value
                if allow_multi
                else PollKind.SINGLE_CHOICE.value
            ),
            results_visibility=PollResultsVisibility.LIVE.value,
            closes_at=closes_at,
            retention_days=30,
        )
        if created_at:
            poll.created_at = created_at
        db.add(poll)

        # Build options + invert option-keyed votes into voter-keyed.
        option_rows: list[tuple[str, PollOption]] = []
        voter_to_options: dict[uuid.UUID, list[uuid.UUID]] = {}
        for position, o_data in enumerate(_list(p_data.get("options"))):
            if not isinstance(o_data, dict):
                continue
            option = PollOption(
                id=uuid.uuid4(),
                poll_id=poll.id,
                text=encrypt(_clean_str(o_data.get("text")) or ""),
                position=position,
            )
            db.add(option)
            option_rows.append((_clean_str(o_data.get("id")) or "", option))
            for vote in _list(o_data.get("votes")):
                if not isinstance(vote, dict):
                    continue
                voter_name = _clean_str(vote.get("member_name"))
                voter = (
                    member_name_to_member.get(voter_name) if voter_name else None
                )
                if voter is None:
                    missing_voter_count += 1
                    continue
                voter_to_options.setdefault(voter.id, []).append(option.id)

        await db.flush()
        for voter_id, option_ids in voter_to_options.items():
            vote_row = PollVote(
                id=uuid.uuid4(),
                poll_id=poll.id,
                voted_as_member_id=voter_id,
                option_ids=option_ids,
            )
            db.add(vote_row)

        imported += 1

    if open_ended_count:
        warnings.append(
            f"{open_ended_count} PluralSpace polls had no close time; set to "
            "one year from creation since Sheaf polls require a close time."
        )
    if missing_voter_count:
        warnings.append(
            f"Dropped {missing_voter_count} poll votes whose voter wasn't imported."
        )

    # Suppress unused variable warning until vote-event support arrives.
    _ = ps_id_to_member
    return imported


# --- Avatar persistence ---------------------------------------------------


async def _persist_avatar_from_zip(
    parsed: _ParsedExport,
    media_path: str,
    db: AsyncSession,
    user: User,
    warnings: list[str],
) -> str | None:
    """Read a media file from the zip, normalize, and store as an UploadedFile.

    Returns the new storage key on success, or None when the bytes are
    unusable, the quota is full, or the user is not allowed to upload
    images on this instance. Failures append a single user-facing
    warning each (deduped where it makes sense).
    """
    if not _user_can_upload_images(user):
        warnings.append(
            "Skipped avatar imports: image uploads are not enabled for this "
            "account."
        )
        return None

    try:
        declared = parsed.zf.getinfo(media_path).file_size
    except KeyError:
        declared = None
    if declared is not None and declared > _MAX_MEDIA_DECOMPRESSED:
        warnings.append(
            f"Avatar file {media_path!r} skipped: bigger than the "
            f"{_MAX_MEDIA_DECOMPRESSED // (1024 * 1024)}MB media limit."
        )
        return None
    raw = parsed.read_media(media_path)
    if raw is None:
        warnings.append(f"Avatar file {media_path!r} was missing from the export.")
        return None
    sniffed = _sniff_image_mime(raw)
    if sniffed is None or sniffed not in _ALLOWED_IMAGE_TYPES:
        warnings.append(
            f"Avatar file {media_path!r} did not match a supported image format."
        )
        return None

    try:
        normalised, mime, _was_animated = await run_in_threadpool(
            normalize_image,
            raw,
            sniffed,
            allow_animation=_animation_allowed(user),
            max_dim=settings.max_image_dimension,
            max_frames=settings.max_animated_frames,
            max_decoded_bytes=settings.max_animated_decoded_bytes,
        )
    except ImageNormalizationError:
        warnings.append(
            f"Could not process avatar {media_path!r} (decode rejected by "
            "normaliser)."
        )
        return None
    size = len(normalised)

    quota = _user_quota_bytes(user)
    if quota > 0:
        used = await db.scalar(
            select(_sum_uploaded_size()).where(UploadedFile.user_id == user.id)
        ) or 0
        if (used + size) > quota:
            warnings.append(
                "Avatar imports stopped: storage quota reached. Remaining "
                "members will be imported without avatars."
            )
            return None

    ext = _MIME_EXT[mime]
    key = f"avatars/{user.id}/{uuid.uuid4().hex}.{ext}"

    storage = get_storage()
    await storage.put(key, normalised, mime)

    db.add(
        UploadedFile(
            user_id=user.id,
            key=key,
            purpose="avatar",
            content_type=mime,
            size_bytes=size,
        )
    )
    return key


def _user_can_upload_images(user: User) -> bool:
    return bool(user.is_admin or settings.allow_image_uploads or user.can_upload_images)


def _animation_allowed(user: User) -> bool:
    """Mirror sheaf.files.animation_allowed for the importer context.

    Re-implemented locally rather than imported so the importer doesn't
    take a runtime dependency on the v1 files module.
    """
    if not settings.allow_animated_uploads:
        return False
    if user.is_admin:
        return True
    return bool(getattr(user, "can_upload_animated_images", False))


def _user_quota_bytes(user: User) -> int:
    quota_map = {
        UserTier.FREE: settings.storage_quota_free_mb,
        UserTier.PLUS: settings.storage_quota_plus_mb,
        UserTier.SELF_HOSTED: settings.storage_quota_selfhosted_mb,
    }
    mb = quota_map.get(user.tier, 0)
    return mb * 1024 * 1024 if mb > 0 else 0


def _sum_uploaded_size():
    from sqlalchemy import func

    return func.coalesce(func.sum(UploadedFile.size_bytes), 0)


# --- Tiny helpers ----------------------------------------------------------


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _clean_str(value: Any) -> str | None:
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
    """Coerce a PluralSpace hex colour to Sheaf's '#rrggbb' shape."""
    s = _clean_str(value)
    if not s:
        return None
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6 or not all(c in "0123456789abcdefABCDEF" for c in s):
        return None
    return f"#{s.lower()}"


def _parse_iso(value: Any) -> datetime | None:
    """Parse a PluralSpace ISO-8601 timestamp into an aware datetime."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

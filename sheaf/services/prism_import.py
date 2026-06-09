"""Prism (.prism) data import.

Prism is an end-to-end encrypted plurality tracker. Its export
format is a custom PRISM1 envelope (decrypted by
`sheaf.services.prism_crypto`) carrying a JSON payload with ~20
top-level entity arrays plus optional XChaCha20-Poly1305 media
blobs.

This module owns the entity walk: validated against a real
format_version 1.0 export. Where Prism's data model doesn't map
cleanly to Sheaf we surface user-facing warning events on the
import detail page rather than dropping silently. Headline mapping
decisions:

- `headmates` -> Member. `notes` becomes the bio markdown,
  `profilePhotoData` (base64) becomes a new UploadedFile via the
  shared normalize_image pipeline. `pkAvatarCachedUrl` is the
  fallback when there's no inline avatar.
- `customColorEnabled`/`customColorHex` -> `Member.color` (only
  when enabled).
- `pluralkitId` -> `Member.pluralkit_id`. PluralKit-side display
  names and proxy tags are dropped (Sheaf doesn't proxy).
- `frontSessions` -> Front (one row, one member). `sessionType` 0
  is a normal front; we don't currently model "always fronting"
  type members separately.
- `sleepSessions` -> dropped with warning. Sheaf doesn't model
  sleep tracking.
- `memberGroups` + `memberGroupEntries` -> Group + group_members.
- `customFields` -> CustomFieldDefinition. `fieldTypeId` text/date/
  number/boolean map directly; slider and other Prism-specific
  types collapse to TEXT with a warning.
- `customFieldValues` -> CustomFieldValue, JSON-encoded value
  carrying whatever shape Prism stored.
- `notes` -> JournalEntry. Per-member entries when `memberId` is
  set; system-wide otherwise.
- `polls` + `pollOptions` + per-option `votes[]` -> Poll +
  PollOption + PollVote (inverted to voter-keyed). Open polls
  (`isClosed: false`) get a one-year close window. Per-vote
  `responseText` (the "Other" option freeform field) is appended
  to the option text since Sheaf's PollVote has no freeform field.
- `conversations` + `messages` -> Sheaf board messages on the
  system board. Each message body is prefixed with the conversation
  title or DM context line so the origin is preserved. One warning
  event per import explains the collapse.
- `memberBoardPosts` -> Member wall messages when `targetMemberId`
  is set, system board posts otherwise.
- `reminders` -> dropped with warning. Sheaf Reminders require a
  notification channel binding which isn't part of the import; the
  user can re-create them after setup.
- `habits` + `habitCompletions` -> dropped with warning. No Sheaf
  surface yet.
- `friends`, `pluralKitSyncState`, `conversationCategories`,
  `frontSessionComments` -> dropped (out of scope or no Sheaf
  equivalent).
- `mediaAttachments` -> UploadedFile per attachment. Each blob is
  XChaCha20-Poly1305-decrypted with its per-blob key from the JSON
  metadata, then run through normalize_image like a regular avatar
  upload (EXIF strip, dim cap, animation gate, quota check).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
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
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User, UserTier
from sheaf.schemas.prism_import import (
    PrismImportResult,
    PrismPreviewMember,
    PrismPreviewSummary,
)
from sheaf.services.custom_fields import encrypt_field_value
from sheaf.services.import_parsing import (
    ImportPayloadError,
    sanitize_external_avatar_url,
)
from sheaf.services.member_limits import enforce_import_member_cap
from sheaf.services.prism_crypto import (
    DecryptedEnvelope,
    decrypt_envelope,
    decrypt_media_blob,
)
from sheaf.storage import get_storage

logger = logging.getLogger("sheaf.imports.prism")


_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _sniff_image_mime(data: bytes) -> str | None:
    """Magic-byte image format sniffer (mirrors files.py)."""
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


# --- Envelope wrapper ------------------------------------------------------


@dataclass
class ParsedPrism:
    """Container surfaced by `parse_envelope_bytes`. Wraps the
    decrypted envelope so callers can pass a single value around
    rather than the {header, json, media_blobs} tuple."""

    envelope: DecryptedEnvelope

    @property
    def data(self) -> dict:
        return self.envelope.json

    @property
    def media_blobs(self) -> dict[str, bytes]:
        return self.envelope.media_blobs


def parse_envelope_bytes(blob: bytes, passphrase: str) -> ParsedPrism:
    """Decrypt the PRISM1 envelope and wrap it for use by preview / import."""
    return ParsedPrism(envelope=decrypt_envelope(blob, passphrase))


# Envelope decryption runs scrypt with parameters taken from the
# untrusted header. prism_crypto caps them (N<=2^17, r<=16, so roughly
# 256MiB and a few hundred ms of CPU per call), but that is still far
# too heavy for the event loop - and the parameters are attacker-
# chosen, so a handful of crafted uploads would otherwise freeze every
# request the worker is serving. Decryptions run in a worker thread,
# a couple at a time so concurrent calls cannot stack the scrypt
# allocations either.
_decrypt_semaphore: asyncio.Semaphore | None = None


def _get_decrypt_semaphore() -> asyncio.Semaphore:
    global _decrypt_semaphore
    if _decrypt_semaphore is None:
        _decrypt_semaphore = asyncio.Semaphore(2)
    return _decrypt_semaphore


async def parse_envelope_bytes_async(blob: bytes, passphrase: str) -> ParsedPrism:
    async with _get_decrypt_semaphore():
        return await asyncio.to_thread(parse_envelope_bytes, blob, passphrase)


# --- Preview ---------------------------------------------------------------


def preview(parsed: ParsedPrism) -> PrismPreviewSummary:
    """Walk the decrypted JSON and return counts + member list summary.

    Doesn't write anything. Used by the synchronous preview endpoint
    so the user can confirm shape before kicking off the async run.
    """
    data = parsed.data
    headmates = _list(data.get("headmates"))
    members: list[PrismPreviewMember] = []
    for m in headmates:
        if not isinstance(m, dict):
            continue
        members.append(
            PrismPreviewMember(
                id=str(m.get("id") or "")[:64],
                name=_clean_str(m.get("name")) or "unnamed",
                is_archived=not bool(m.get("isActive", True)),
                has_avatar=bool(
                    _clean_str(m.get("profilePhotoData"))
                    or _clean_str(m.get("pkAvatarCachedUrl"))
                ),
                pluralkit_id=_clean_str(m.get("pluralkitId")),
            )
        )

    sys_block = _list(data.get("systemSettings"))
    sys_block = sys_block[0] if sys_block and isinstance(sys_block[0], dict) else {}
    sys_name = _clean_str(sys_block.get("systemName"))

    return PrismPreviewSummary(
        system_name=sys_name,
        format_version=_clean_str(data.get("formatVersion")),
        export_date=_parse_iso(data.get("exportDate")),
        app_name=_clean_str(data.get("appName")),
        member_count=len(members),
        members=members,
        group_count=len(_list(data.get("memberGroups"))),
        custom_field_count=len(_list(data.get("customFields"))),
        front_session_count=len(_list(data.get("frontSessions"))),
        sleep_session_count=len(_list(data.get("sleepSessions"))),
        conversation_count=len(_list(data.get("conversations"))),
        message_count=len(_list(data.get("messages"))),
        poll_count=len(_list(data.get("polls"))),
        poll_option_count=len(_list(data.get("pollOptions"))),
        note_count=len(_list(data.get("notes"))),
        reminder_count=len(_list(data.get("reminders"))),
        habit_count=len(_list(data.get("habits"))),
        member_board_post_count=len(_list(data.get("memberBoardPosts"))),
        media_attachment_count=len(_list(data.get("mediaAttachments"))),
        media_blob_count=len(parsed.media_blobs),
    )


# --- Import driver ---------------------------------------------------------


@dataclass
class _MemberHandle:
    """Track each imported member alongside the bits of its source
    record we may need later (plaintext name for author attribution
    when we don't want to re-decrypt the encrypted Member row).
    """

    member: Member
    plaintext_name: str
    source: dict = field(default_factory=dict)


async def run_import(
    parsed: ParsedPrism,
    system: System,
    user: User,
    db: AsyncSession,
    *,
    system_profile: bool = True,
    member_ids: list[str] | None = None,
    member_avatars: bool = True,
    roles_as_tags: bool = True,
    member_groups: bool = True,
    custom_fields: bool = True,
    front_sessions: bool = True,
    sleep_sessions: bool = True,
    notes: bool = True,
    polls: bool = True,
    reminders: bool = True,
    habits: bool = True,
    conversations: bool = True,
    member_board_posts: bool = True,
    media_attachments: bool = True,
) -> PrismImportResult:
    """Import a decrypted Prism envelope into the user's system.

    Returns a result with per-section counts and a `warnings` list of
    user-facing messages. The runner converts each warning to a
    `level=warning, stage=import` event.
    """
    result = PrismImportResult()
    data = parsed.data

    if system_profile:
        _apply_system_profile(data, system)

    selected = set(member_ids) if member_ids is not None else None

    # Pre-filter to the rows that will actually become Member records so
    # the tier member-cap check counts exactly what the loop below would
    # write. Hard-fails (clean job error) before anything is written.
    eligible: list[dict] = []
    for m in _list(data.get("headmates")):
        if not isinstance(m, dict):
            continue
        ps_id = _clean_str(m.get("id"))
        if not ps_id:
            result.warnings.append("Skipped a headmate with no id.")
            continue
        if selected is not None and ps_id not in selected:
            continue
        eligible.append(m)
    await enforce_import_member_cap(db, system, len(eligible))

    ps_id_to_handle: dict[str, _MemberHandle] = {}
    for m in eligible:
        ps_id = _clean_str(m.get("id"))
        plaintext_name = (_clean_str(m.get("name")) or "unnamed")[:100]
        plaintext_description = _clean_str(m.get("notes"))
        custom_color = _normalize_color(m.get("customColorHex")) if m.get(
            "customColorEnabled"
        ) else None
        display_name = _clean_str(m.get("displayName")) or _clean_str(
            m.get("pluralkitDisplayName")
        )
        member = Member(
            id=uuid.uuid4(),
            system_id=system.id,
            name=encrypt(plaintext_name),
            name_hash=blind_index(plaintext_name),
            display_name=_truncate(display_name, 100),
            description=(
                encrypt(plaintext_description) if plaintext_description else None
            ),
            pronouns=_truncate(_clean_str(m.get("pronouns")), 100),
            color=custom_color,
            birthday=_clean_str(m.get("birthday")),
            pluralkit_id=_truncate(_clean_str(m.get("pluralkitId")), 8),
            emoji=_truncate(_clean_str(m.get("emoji")), 8),
            is_custom_front=False,
            privacy=PrivacyLevel.PRIVATE,
        )
        created_at = _parse_iso(m.get("createdAt"))
        if created_at:
            member.created_at = created_at
        db.add(member)
        ps_id_to_handle[ps_id] = _MemberHandle(
            member=member, plaintext_name=plaintext_name, source=m
        )
        result.members_imported += 1

    if not ps_id_to_handle:
        return result

    await db.flush()

    # Note: `roles_as_tags` is wired through the options for symmetry
    # with the PluralSpace importer, but Prism headmates don't carry
    # an explicit role field. Reserved for a future Prism schema bump.
    _ = roles_as_tags

    if member_avatars:
        external_avatars_skipped = 0
        for ps_id, handle in ps_id_to_handle.items():
            src = handle.source
            inline_b64 = _clean_str(src.get("profilePhotoData"))
            if inline_b64:
                stored_key = await _persist_avatar_from_b64(
                    inline_b64, ps_id, db, user, result.warnings
                )
                if stored_key:
                    handle.member.avatar_url = stored_key[:500]
                    result.avatars_imported += 1
                    continue
            cached_url = _clean_str(src.get("pkAvatarCachedUrl"))
            if cached_url:
                # Fallback external reference (Prism's cached PluralKit
                # avatar URL); kept only when the scheme is plain http(s)
                # and the instance allows external images.
                safe_url = sanitize_external_avatar_url(cached_url)
                if safe_url:
                    handle.member.avatar_url = safe_url
                else:
                    external_avatars_skipped += 1
        if external_avatars_skipped:
            result.warnings.append(
                f"Skipped {external_avatars_skipped} external avatar "
                "link(s): external images are not allowed on this server, "
                "or the link was not a plain http(s) URL."
            )

    if member_groups:
        result.groups_imported += await _import_groups(
            _list(data.get("memberGroups")),
            _list(data.get("memberGroupEntries")),
            ps_id_to_handle,
            system.id,
            db,
            result.warnings,
        )

    if custom_fields:
        cf_imported, cfv_imported = await _import_custom_fields(
            _list(data.get("customFields")),
            _list(data.get("customFieldValues")),
            ps_id_to_handle,
            system.id,
            db,
            result.warnings,
        )
        result.custom_fields_imported += cf_imported
        result.custom_field_values_imported += cfv_imported

    if front_sessions:
        result.fronts_imported += await _import_front_sessions(
            _list(data.get("frontSessions")),
            ps_id_to_handle,
            system.id,
            db,
            result.warnings,
        )

    if notes:
        result.journals_imported += await _import_notes(
            _list(data.get("notes")),
            ps_id_to_handle,
            system,
            db,
        )

    if polls:
        result.polls_imported += await _import_polls(
            _list(data.get("polls")),
            _list(data.get("pollOptions")),
            ps_id_to_handle,
            system.id,
            db,
            result.warnings,
        )

    if conversations:
        result.messages_imported += await _import_conversations(
            _list(data.get("conversations")),
            _list(data.get("messages")),
            ps_id_to_handle,
            system.id,
            db,
            result.warnings,
        )

    if member_board_posts:
        result.board_posts_imported += await _import_member_board_posts(
            _list(data.get("memberBoardPosts")),
            ps_id_to_handle,
            system.id,
            db,
            result.warnings,
        )

    if media_attachments:
        result.media_attachments_imported += await _import_media_attachments(
            _list(data.get("mediaAttachments")),
            parsed.media_blobs,
            db,
            user,
            result.warnings,
        )

    # Sections we deliberately skip with a single warning each.
    sleep_count = len(_list(data.get("sleepSessions")))
    if sleep_sessions and sleep_count:
        result.warnings.append(
            f"Skipped {sleep_count} sleep sessions: Sheaf doesn't model "
            "sleep tracking yet."
        )
    habit_count = len(_list(data.get("habits")))
    if habits and habit_count:
        result.warnings.append(
            f"Skipped {habit_count} habits + their completions: Sheaf "
            "doesn't have a habits surface yet."
        )
    reminder_count = len(_list(data.get("reminders")))
    if reminders and reminder_count:
        result.warnings.append(
            f"Skipped {reminder_count} reminders: Sheaf reminders need a "
            "notification channel binding that's set up after import. "
            "Re-create them via the Notifications screen."
        )
    if data.get("friends"):
        result.warnings.append(
            "Skipped friends / cross-system data: Sheaf doesn't have a "
            "friends model."
        )
    if data.get("conversationCategories"):
        result.warnings.append(
            "Skipped conversation categories: Sheaf has one system board "
            "rather than categorised channels."
        )
    if data.get("frontSessionComments"):
        result.warnings.append(
            "Skipped front-session comments: Sheaf doesn't have a per-"
            "session comments thread (front custom_status is the closest "
            "equivalent and is preserved when present)."
        )

    return result


# --- Section helpers -------------------------------------------------------


def _apply_system_profile(data: dict, system: System) -> None:
    """Pull the systemSettings[0] block onto the importing system.

    Only fills empty Sheaf fields; never overwrites a name the user
    already set on their Sheaf system.
    """
    settings_arr = _list(data.get("systemSettings"))
    if not settings_arr or not isinstance(settings_arr[0], dict):
        return
    block = settings_arr[0]
    name = _clean_str(block.get("systemName"))
    if name and not system.name:
        system.name = name[:100]
    description = _clean_str(block.get("systemDescription"))
    if description and not system.description:
        system.description = description
    color = _normalize_color(block.get("systemColor")) or _normalize_color(
        block.get("accentColorHex")
    )
    if color and not system.color:
        system.color = color


async def _import_groups(
    groups_in: list,
    entries_in: list,
    ps_id_to_handle: dict[str, _MemberHandle],
    system_id: uuid.UUID,
    db: AsyncSession,
    warnings: list[str],
) -> int:
    """Create Sheaf Groups + group_members from Prism's two-table model.

    Deduped against existing system groups by case-folded name so a
    re-import doesn't grow the group list.
    """
    existing = await db.execute(select(Group).where(Group.system_id == system_id))
    by_name: dict[str, Group] = {g.name.lower(): g for g in existing.scalars().all()}
    by_prism_id: dict[str, Group] = {}
    created = 0
    for g in groups_in:
        if not isinstance(g, dict):
            continue
        name = _clean_str(g.get("name"))
        if not name:
            continue
        key = name.lower()
        group = by_name.get(key)
        if group is None:
            group = Group(
                id=uuid.uuid4(),
                system_id=system_id,
                name=name[:100],
                description=_clean_str(g.get("description")),
                color=_normalize_color(g.get("colorHex")),
            )
            db.add(group)
            by_name[key] = group
            created += 1
        prism_id = _clean_str(g.get("id"))
        if prism_id:
            by_prism_id[prism_id] = group

    await db.flush()

    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for entry in entries_in:
        if not isinstance(entry, dict):
            continue
        gid = _clean_str(entry.get("groupId"))
        mid = _clean_str(entry.get("memberId"))
        group = by_prism_id.get(gid) if gid else None
        handle = ps_id_to_handle.get(mid) if mid else None
        if group is None or handle is None:
            continue
        pair = (group.id, handle.member.id)
        if pair in seen:
            continue
        seen.add(pair)
        await db.execute(
            group_members.insert().values(
                group_id=group.id, member_id=handle.member.id
            )
        )

    _ = warnings  # nothing to surface from groups today; reserved for shape drift
    return created


def _map_prism_field_type(raw: Any) -> tuple[FieldType, bool]:
    """Coerce Prism's `fieldTypeId` string to a Sheaf FieldType.

    Returns `(FieldType, surfaced_as_text)`. Prism-specific types
    (slider, etc.) collapse to TEXT with the second tuple element
    set so the caller can surface a warning.
    """
    if not isinstance(raw, str):
        return FieldType.TEXT, True
    norm = raw.strip().lower()
    if norm in ("text",):
        return FieldType.TEXT, False
    if norm in ("date",):
        return FieldType.DATE, False
    if norm in ("number", "integer", "int"):
        return FieldType.NUMBER, False
    if norm in ("boolean", "bool"):
        return FieldType.BOOLEAN, False
    return FieldType.TEXT, True


async def _import_custom_fields(
    fields_in: list,
    values_in: list,
    ps_id_to_handle: dict[str, _MemberHandle],
    system_id: uuid.UUID,
    db: AsyncSession,
    warnings: list[str],
) -> tuple[int, int]:
    """Create Sheaf CustomFieldDefinitions + per-member values.

    Dedupes definitions against existing ones on (lowercased name,
    field_type). Unrecognised Prism field types fall through to TEXT
    with a warning.
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
    by_prism_id: dict[str, CustomFieldDefinition] = {}
    created = 0
    surfaced_types: set[str] = set()
    for f in fields_in:
        if not isinstance(f, dict):
            continue
        name = _clean_str(f.get("name"))
        if not name:
            continue
        ftype, surfaced = _map_prism_field_type(f.get("fieldTypeId"))
        if surfaced:
            surfaced_types.add(str(f.get("fieldTypeId") or "<unknown>"))
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
        prism_id = _clean_str(f.get("id"))
        if prism_id:
            by_prism_id[prism_id] = field_def

    if surfaced_types:
        warnings.append(
            "Prism custom fields of type "
            f"{sorted(surfaced_types)} were imported as plain text "
            "(Sheaf doesn't have a slider / sticker / etc. equivalent)."
        )

    await db.flush()

    values_imported = 0
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for v in values_in:
        if not isinstance(v, dict):
            continue
        field_id = _clean_str(v.get("fieldId")) or _clean_str(v.get("customFieldId"))
        member_id = _clean_str(v.get("memberId")) or _clean_str(v.get("headmateId"))
        field_def = by_prism_id.get(field_id) if field_id else None
        handle = ps_id_to_handle.get(member_id) if member_id else None
        if field_def is None or handle is None:
            continue
        pair = (field_def.id, handle.member.id)
        if pair in seen:
            continue
        seen.add(pair)
        raw_value = v.get("value")
        if raw_value is None or raw_value == "":
            continue
        cfv = CustomFieldValue(
            id=uuid.uuid4(),
            field_id=field_def.id,
            member_id=handle.member.id,
            value=encrypt_field_value(raw_value),
        )
        db.add(cfv)
        values_imported += 1
    return created, values_imported


async def _import_front_sessions(
    sessions_in: list,
    ps_id_to_handle: dict[str, _MemberHandle],
    system_id: uuid.UUID,
    db: AsyncSession,
    warnings: list[str],
) -> int:
    """Each Prism frontSession is one-member fronting, one Sheaf Front."""
    imported = 0
    missing = 0
    for s in sessions_in:
        if not isinstance(s, dict):
            continue
        started_at = _parse_iso(s.get("startTime"))
        if started_at is None:
            warnings.append(
                f"Skipped front session {_clean_str(s.get('id')) or '<no-id>'}: "
                "invalid startTime."
            )
            continue
        ended_at = _parse_iso(s.get("endTime"))
        hm_id = _clean_str(s.get("headmateId"))
        handle = ps_id_to_handle.get(hm_id) if hm_id else None
        if handle is None:
            missing += 1
            continue
        front = Front(
            id=uuid.uuid4(),
            system_id=system_id,
            started_at=started_at,
            ended_at=ended_at,
        )
        db.add(front)
        await db.flush()
        await db.execute(
            front_members.insert().values(
                front_id=front.id, member_id=handle.member.id
            )
        )
        imported += 1
    if missing:
        warnings.append(
            f"Skipped {missing} front sessions whose headmate wasn't imported."
        )
    return imported


async def _import_notes(
    notes_in: list,
    ps_id_to_handle: dict[str, _MemberHandle],
    system: System,
    db: AsyncSession,
) -> int:
    """Prism notes (per-member or system-wide) map onto Sheaf journals."""
    imported = 0
    for n in notes_in:
        if not isinstance(n, dict):
            continue
        title = _clean_str(n.get("title"))
        body = _clean_str(n.get("body")) or ""
        member_id_str = _clean_str(n.get("memberId"))
        handle = ps_id_to_handle.get(member_id_str) if member_id_str else None
        author_names: list[str] = []
        author_ids: list[str] = []
        if handle is None and member_id_str:
            # Member referenced but not imported. Fall back to a system journal.
            pass
        elif handle is not None:
            author_ids.append(str(handle.member.id))
            author_names.append(handle.plaintext_name)
        entry = JournalEntry(
            id=uuid.uuid4(),
            system_id=system.id,
            member_id=handle.member.id if handle else None,
            title=encrypt(title) if title else None,
            body=encrypt(body),
            visibility="system",
            author_user_id=system.user_id,
            author_member_ids=author_ids,
            author_member_names=author_names,
            image_keys=[],
        )
        created = _parse_iso(n.get("createdAt"))
        if created:
            entry.created_at = created
        updated = _parse_iso(n.get("modifiedAt"))
        if updated:
            entry.updated_at = updated
        db.add(entry)
        imported += 1
    return imported


async def _import_polls(
    polls_in: list,
    options_in: list,
    ps_id_to_handle: dict[str, _MemberHandle],
    system_id: uuid.UUID,
    db: AsyncSession,
    warnings: list[str],
) -> int:
    """Map Prism polls + sibling pollOptions to Sheaf Poll/Option/Vote rows.

    Prism's poll options sit in a top-level `pollOptions[]` array
    keyed by `pollId`; votes live on each option. We invert to
    Sheaf's per-voter PollVote with `option_ids`. `isClosed=false`
    polls get a one-year close window since Sheaf polls require one.
    `responseText` on "other" option votes is appended to the option
    text (Sheaf has no per-vote freeform field).
    """
    imported = 0
    open_ended = 0
    anonymous_count = 0
    response_text_dropped = 0
    missing_voter = 0

    options_by_poll: dict[str, list[dict]] = {}
    for o in options_in:
        if not isinstance(o, dict):
            continue
        pid = _clean_str(o.get("pollId"))
        if pid:
            options_by_poll.setdefault(pid, []).append(o)

    for p in polls_in:
        if not isinstance(p, dict):
            continue
        prism_id = _clean_str(p.get("id"))
        question = _clean_str(p.get("question")) or ""
        description = _clean_str(p.get("description"))
        allow_multi = bool(p.get("allowsMultipleVotes"))
        is_closed = bool(p.get("isClosed"))
        if bool(p.get("isAnonymous")):
            anonymous_count += 1
        created_at = _parse_iso(p.get("createdAt"))
        if is_closed:
            closes_at = created_at or datetime.now(UTC)
        else:
            base = created_at or datetime.now(UTC)
            closes_at = base + timedelta(days=365)
            open_ended += 1
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

        opts = options_by_poll.get(prism_id or "", [])
        opts.sort(key=lambda o: (o.get("sortOrder") or 0))
        voter_to_options: dict[uuid.UUID, list[uuid.UUID]] = {}
        for position, o_data in enumerate(opts):
            if not isinstance(o_data, dict):
                continue
            text = _clean_str(o_data.get("text")) or ""
            response_texts: list[str] = []
            for vote in _list(o_data.get("votes")):
                if not isinstance(vote, dict):
                    continue
                voter_id = _clean_str(vote.get("memberId"))
                handle = ps_id_to_handle.get(voter_id) if voter_id else None
                if handle is None:
                    missing_voter += 1
                    continue
                rt = _clean_str(vote.get("responseText"))
                if rt:
                    response_texts.append(f"{handle.plaintext_name}: {rt}")
                    response_text_dropped += 1
                option_id = uuid.uuid4()
                voter_to_options.setdefault(handle.member.id, []).append(option_id)
                # Stash on the per-vote entry — we'll resolve below.
                vote["_sheaf_option_id"] = option_id
            display_text = text
            if response_texts:
                display_text = f"{text}\n---\n" + "\n".join(response_texts)
            option = PollOption(
                id=uuid.uuid4(),
                poll_id=poll.id,
                text=encrypt(display_text),
                position=position,
            )
            db.add(option)
            # Map staged voter→option_id to the real PollOption.id.
            for vote in _list(o_data.get("votes")):
                if not isinstance(vote, dict):
                    continue
                staged = vote.pop("_sheaf_option_id", None)
                if staged is None:
                    continue
                voter_id = _clean_str(vote.get("memberId"))
                handle = ps_id_to_handle.get(voter_id) if voter_id else None
                if handle is None:
                    continue
                ids = voter_to_options.get(handle.member.id, [])
                for i, v in enumerate(ids):
                    if v == staged:
                        ids[i] = option.id

        await db.flush()
        for voter_id, option_ids in voter_to_options.items():
            db.add(
                PollVote(
                    id=uuid.uuid4(),
                    poll_id=poll.id,
                    voted_as_member_id=voter_id,
                    option_ids=option_ids,
                )
            )
        imported += 1

    if open_ended:
        warnings.append(
            f"{open_ended} Prism polls were still open; Sheaf polls need a "
            "close time so each was given a one-year window from creation."
        )
    if anonymous_count:
        warnings.append(
            f"{anonymous_count} Prism polls were marked anonymous; Sheaf "
            "polls always record the voting member. Import preserved votes "
            "with attribution."
        )
    if response_text_dropped:
        warnings.append(
            f"{response_text_dropped} freeform 'Other' poll responses were "
            "folded into the option text since Sheaf PollVotes have no "
            "freeform response field."
        )
    if missing_voter:
        warnings.append(
            f"Dropped {missing_voter} poll votes whose voter wasn't imported."
        )
    return imported


async def _import_conversations(
    convs_in: list,
    msgs_in: list,
    ps_id_to_handle: dict[str, _MemberHandle],
    system_id: uuid.UUID,
    db: AsyncSession,
    warnings: list[str],
) -> int:
    """Collapse Prism conversations + messages to the Sheaf system board.

    Each message body is prefixed with `[DM with X]` for direct
    messages or `[Chat: <title>]` for group conversations so the
    origin survives the collapse. One warning event per import
    explains the collapse.
    """
    convs_by_id: dict[str, dict] = {}
    for c in convs_in:
        if not isinstance(c, dict):
            continue
        cid = _clean_str(c.get("id"))
        if cid:
            convs_by_id[cid] = c
    if not convs_by_id and not msgs_in:
        return 0
    has_dm = any(
        isinstance(c, dict)
        and (
            c.get("isDirectMessage")
            or _clean_str(c.get("type")) == "directmessage"
        )
        for c in convs_in
    )
    has_group = any(
        isinstance(c, dict)
        and not (
            c.get("isDirectMessage")
            or _clean_str(c.get("type")) == "directmessage"
        )
        for c in convs_in
    )
    if has_dm or has_group:
        warnings.append(
            "Prism conversations collapsed onto the Sheaf system board: "
            "each message body now carries a `[DM ...]` or `[Chat: ...]` "
            "prefix so the original thread is recoverable."
        )

    imported = 0
    missing_author = 0
    for m in msgs_in:
        if not isinstance(m, dict):
            continue
        cid = _clean_str(m.get("conversationId"))
        conv = convs_by_id.get(cid) if cid else None
        body = _clean_str(m.get("content")) or ""
        if conv:
            if conv.get("isDirectMessage") or _clean_str(conv.get("type")) == "directmessage":
                participants = _list(conv.get("participantIds"))
                names = []
                for pid in participants:
                    pid_s = _clean_str(pid)
                    handle = ps_id_to_handle.get(pid_s) if pid_s else None
                    if handle:
                        names.append(handle.plaintext_name)
                label = " <-> ".join(names) if names else "unknown participants"
                body = f"[DM: {label}] {body}".rstrip()
            else:
                title = _clean_str(conv.get("title")) or "untitled"
                body = f"[Chat: {title}] {body}".rstrip()
        author_id = _clean_str(m.get("authorId"))
        author_handle = ps_id_to_handle.get(author_id) if author_id else None
        if author_id and author_handle is None:
            missing_author += 1
        message = Message(
            id=uuid.uuid4(),
            system_id=system_id,
            board_kind=BoardKind.SYSTEM.value,
            board_member_id=None,
            author_member_id=author_handle.member.id if author_handle else None,
            body=encrypt(body),
        )
        ts = _parse_iso(m.get("timestamp"))
        if ts:
            message.created_at = ts
        db.add(message)
        imported += 1
    if missing_author:
        warnings.append(
            f"{missing_author} chat messages referenced an author that wasn't "
            "imported; those messages were attributed to nobody."
        )
    return imported


async def _import_member_board_posts(
    posts_in: list,
    ps_id_to_handle: dict[str, _MemberHandle],
    system_id: uuid.UUID,
    db: AsyncSession,
    warnings: list[str],
) -> int:
    """Prism `memberBoardPosts` map to Sheaf board posts.

    `targetMemberId` present -> per-member wall. Absent -> system
    board. `audience` is preserved as a body prefix when not the
    default ("public" / unset).
    """
    imported = 0
    missing_target = 0
    deleted_skipped = 0
    for p in posts_in:
        if not isinstance(p, dict):
            continue
        if bool(p.get("isDeleted")):
            deleted_skipped += 1
            continue
        author_id = _clean_str(p.get("authorId"))
        author_handle = ps_id_to_handle.get(author_id) if author_id else None
        target_id = _clean_str(p.get("targetMemberId"))
        target_handle = ps_id_to_handle.get(target_id) if target_id else None
        if target_id and target_handle is None:
            missing_target += 1
            continue
        title = _clean_str(p.get("title"))
        body = _clean_str(p.get("body")) or ""
        audience = _clean_str(p.get("audience"))
        if audience and audience != "public":
            body = f"[audience: {audience}] {body}".rstrip()
        if title:
            body = f"**{title}**\n\n{body}"
        message = Message(
            id=uuid.uuid4(),
            system_id=system_id,
            board_kind=(
                BoardKind.MEMBER.value if target_handle else BoardKind.SYSTEM.value
            ),
            board_member_id=target_handle.member.id if target_handle else None,
            author_member_id=author_handle.member.id if author_handle else None,
            body=encrypt(body),
        )
        written = _parse_iso(p.get("writtenAt")) or _parse_iso(p.get("createdAt"))
        if written:
            message.created_at = written
        db.add(message)
        imported += 1
    if deleted_skipped:
        warnings.append(
            f"Skipped {deleted_skipped} member board posts marked deleted."
        )
    if missing_target:
        warnings.append(
            f"Skipped {missing_target} board posts whose target member wasn't "
            "imported."
        )
    return imported


async def _import_media_attachments(
    atts_in: list,
    blobs: dict[str, bytes],
    db: AsyncSession,
    user: User,
    warnings: list[str],
) -> int:
    """Decrypt + store each referenced media blob as an UploadedFile.

    Per-blob keys live alongside the metadata in the JSON; we look
    up the ciphertext by `mediaId` in the blob registry from the
    envelope.
    """
    if not atts_in:
        return 0
    if not _user_can_upload_images(user):
        warnings.append(
            "Skipped media attachment imports: image uploads are not "
            "enabled for this account."
        )
        return 0
    imported = 0
    quota_warned = False
    for att in atts_in:
        if not isinstance(att, dict):
            continue
        media_id = _clean_str(att.get("mediaId"))
        key_b64 = _clean_str(att.get("encryptionKeyB64"))
        if not media_id or not key_b64:
            warnings.append(
                "Skipped media attachment missing mediaId or encryption key."
            )
            continue
        blob = blobs.get(media_id)
        if blob is None:
            warnings.append(
                f"Media blob {media_id!r} referenced by an attachment was "
                "missing from the envelope."
            )
            continue
        try:
            plaintext = decrypt_media_blob(blob, key_b64)
        except ImportPayloadError as exc:
            warnings.append(
                f"Could not decrypt media blob {media_id!r}: {exc}"
            )
            continue
        sniffed = _sniff_image_mime(plaintext)
        if sniffed is None or sniffed not in _ALLOWED_IMAGE_TYPES:
            warnings.append(
                f"Media attachment {media_id!r} is not a supported image "
                "format (Sheaf only ingests image attachments at the "
                "moment)."
            )
            continue
        try:
            normalised, mime, _was_animated = await run_in_threadpool(
                normalize_image,
                plaintext,
                sniffed,
                allow_animation=_animation_allowed(user),
                max_dim=settings.max_image_dimension,
                max_frames=settings.max_animated_frames,
                max_decoded_bytes=settings.max_animated_decoded_bytes,
            )
        except ImageNormalizationError:
            warnings.append(
                f"Media attachment {media_id!r} was rejected by the image "
                "normaliser."
            )
            continue
        size = len(normalised)

        quota = _user_quota_bytes(user)
        if quota > 0:
            from sqlalchemy import func

            used = await db.scalar(
                select(func.coalesce(func.sum(UploadedFile.size_bytes), 0)).where(
                    UploadedFile.user_id == user.id
                )
            ) or 0
            if (used + size) > quota:
                if not quota_warned:
                    warnings.append(
                        "Media attachment imports stopped: storage quota "
                        "reached."
                    )
                    quota_warned = True
                continue

        ext = _MIME_EXT[mime]
        key = f"avatars/{user.id}/{uuid.uuid4().hex}.{ext}"
        await get_storage().put(key, normalised, mime)
        db.add(
            UploadedFile(
                user_id=user.id,
                key=key,
                purpose="bio",
                content_type=mime,
                size_bytes=size,
            )
        )
        imported += 1
    return imported


# --- Avatar persistence ----------------------------------------------------


async def _persist_avatar_from_b64(
    payload_b64: str,
    ps_id: str,
    db: AsyncSession,
    user: User,
    warnings: list[str],
) -> str | None:
    """Decode `profilePhotoData`, normalize, store as an UploadedFile.

    Returns the storage key on success; None when image uploads are
    disabled, the bytes don't decode, or the quota is full.
    """
    if not _user_can_upload_images(user):
        if not any(w.startswith("Skipped avatar imports") for w in warnings):
            warnings.append(
                "Skipped avatar imports: image uploads are not enabled for "
                "this account."
            )
        return None
    try:
        payload = payload_b64.replace("\n", "").replace("\r", "")
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError):
        warnings.append(
            f"Headmate {ps_id!r} avatar was not valid base64; skipped."
        )
        return None
    sniffed = _sniff_image_mime(raw)
    if sniffed is None or sniffed not in _ALLOWED_IMAGE_TYPES:
        warnings.append(
            f"Headmate {ps_id!r} avatar is not a supported image format."
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
            f"Headmate {ps_id!r} avatar was rejected by the image normaliser."
        )
        return None
    size = len(normalised)

    quota = _user_quota_bytes(user)
    if quota > 0:
        from sqlalchemy import func

        used = await db.scalar(
            select(func.coalesce(func.sum(UploadedFile.size_bytes), 0)).where(
                UploadedFile.user_id == user.id
            )
        ) or 0
        if (used + size) > quota:
            warnings.append(
                "Avatar imports stopped: storage quota reached. Remaining "
                "members will be imported without avatars."
            )
            return None

    ext = _MIME_EXT[mime]
    key = f"avatars/{user.id}/{uuid.uuid4().hex}.{ext}"
    await get_storage().put(key, normalised, mime)
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
    """Coerce a hex colour to Sheaf's `#rrggbb` shape."""
    s = _clean_str(value)
    if not s:
        return None
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6 or not all(c in "0123456789abcdefABCDEF" for c in s):
        return None
    return f"#{s.lower()}"


def _parse_iso(value: Any) -> datetime | None:
    """Parse Prism's ISO-8601 timestamps (with `Z` suffix or `+00:00`)."""
    if not isinstance(value, str):
        return None
    s = value.replace("Z", "+00:00")
    # Prism timestamps sometimes carry microseconds with > 6 digits.
    # fromisoformat tolerates 0/3/6 — split trailing junk if any.
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        if "." in s and "+" in s:
            head, _, tail = s.rpartition("+")
            stem, dot, frac = head.partition(".")
            if dot and len(frac) > 6:
                head = f"{stem}.{frac[:6]}"
                try:
                    return datetime.fromisoformat(f"{head}+{tail}")
                except ValueError:
                    return None
        return None



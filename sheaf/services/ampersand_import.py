"""Ampersand JSON data import service.

Ampersand (https://codeberg.org/Ampersand/app) exports a
``{ revision, config, database }`` JSON document; ``database`` is an
object of per-table arrays (members, systems, customFields, tags,
frontingEntries, journalPosts, notes, boardMessages, reminders, assets,
filterQueries). Images (member/system ``image``/``cover``, asset
``file``, journal ``cover``) arrive as inline base64 ``data:`` URIs.

The mapping decisions live in ``../sheaf-design-docs/ampersand-import.md``
(see the 2026-07-14 "Implementation decisions" section) and the
per-app gap table in ``feature-gaps.md``. Headlines:

- Every Ampersand ``System`` becomes a Sheaf ``Group`` (nested via
  ``parent``); a member's ``system`` FK becomes group membership.
- Each ``FrontingEntry`` becomes one single-member ``Front`` (no
  overlap-coalescing).
- ``role`` -> member Tag, ``age`` -> an auto-created "Age" custom field.
- Inline images are decoded and pushed through the shared
  ``store_imported_image`` pipeline; ``config`` (incl. the app-lock
  password hash) is never touched.
"""

import base64
import binascii
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import blind_index, encrypt
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue, FieldType
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
from sheaf.models.notification_channel import (
    DestinationState,
    DestinationType,
    NotificationChannel,
)
from sheaf.models.poll import Poll, PollKind, PollOption, PollResultsVisibility, PollVote
from sheaf.models.reminder import Reminder
from sheaf.models.system import System
from sheaf.models.tag import Tag
from sheaf.models.user import User
from sheaf.models.watch_token import WatchToken
from sheaf.schemas.ampersand_import import (
    AmpersandImportOptions,
    AmpersandImportResult,
    AmpersandPreviewSummary,
)
from sheaf.services import import_limits as il
from sheaf.services.custom_fields import encrypt_field_value
from sheaf.services.import_content_dedup import (
    ContentMatchIndex,
    front_key,
    load_front_index,
    load_group_index,
    normalize_front_interval,
)
from sheaf.services.import_dedup import (
    ImportConflictStrategy,
    candidate_key,
    count_new_members,
    load_member_match_index,
    resolve_member,
)
from sheaf.services.import_limits import ClampReport, clamp_str
from sheaf.services.import_media import (
    ImportImageError,
    StoredImportImage,
    store_imported_image,
    user_can_upload_images,
)
from sheaf.services.member_limits import enforce_import_member_cap

logger = logging.getLogger("sheaf.imports.ampersand")

# Poll close window for imported polls. Ampersand board polls have no
# close concept; import them "open" for a year like the PluralSpace /
# Prism importers so a historical poll's votes aren't reaped by the
# retention sweep the instant it lands (closes_at in the past + a short
# retention would delete it).
_POLL_OPEN_DAYS = 365
_POLL_RETENTION_DAYS = 365


# --- Parsing helpers --------------------------------------------------------


def _database(data: dict) -> dict:
    """The ``database`` object, or an empty dict for a malformed export."""
    db = data.get("database")
    return db if isinstance(db, dict) else {}


def _coll(data: dict, name: str) -> list[dict]:
    """A ``database`` collection as a list of dict rows; missing -> []."""
    raw = _database(data).get(name)
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def _coerce_str(value: object) -> str | None:
    """Plain string, or None for null / non-string. Never str()-quotes a
    non-string so member content can't leak into an error via coercion."""
    return value if isinstance(value, str) else None


def _parse_iso(value: object) -> datetime | None:
    """Parse an Ampersand ISO-8601 timestamp to an aware UTC datetime.

    Ampersand serialises Dates via ``toISOString()`` (e.g.
    ``2026-03-19T21:35:57.243Z``). ``fromisoformat`` handles the trailing
    ``Z`` and milliseconds on 3.11+. Naive values are treated as UTC.
    Missing / malformed -> None so one bad row can't crash the walk."""
    s = _coerce_str(value)
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


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


_DATA_URI_RE = re.compile(
    r"^data:[\w/+.\-]*?(?:;[\w-]+=[\w-]+)*;base64,(?P<b64>.*)$",
    re.DOTALL,
)


def _decode_data_uri(value: object) -> bytes | None:
    """Decode a base64 ``data:`` URI to bytes, or None.

    Only base64 payloads are handled (Ampersand always uses them via
    ``toDataURI``). The image bytes are handed to ``store_imported_image``,
    which sniffs the real format and re-encodes, so a lying mime type in
    the URI is harmless. Bounded by the 100MB request-body cap upstream."""
    s = _coerce_str(value)
    if not s:
        return None
    m = _DATA_URI_RE.match(s.strip())
    if not m:
        return None
    try:
        return base64.b64decode(m.group("b64"), validate=True)
    except (binascii.Error, ValueError):
        return None


# --- Preview ----------------------------------------------------------------


def _count_polls(data: dict) -> int:
    return sum(1 for b in _coll(data, "boardMessages") if isinstance(b.get("poll"), dict))


def preview(data: dict) -> AmpersandPreviewSummary:
    """Parse an Ampersand export and summarise importable data. No writes."""
    members = _coll(data, "members")
    real_members = [m for m in members if not m.get("isCustomFront")]
    custom_fronts = [m for m in members if m.get("isCustomFront")]
    journals = _coll(data, "journalPosts")
    notes = _coll(data, "notes")
    board = _coll(data, "boardMessages")

    summary = AmpersandPreviewSummary(
        system_count=len(_coll(data, "systems")),
        member_count=len(real_members),
        custom_front_count=len(custom_fronts),
        front_history_count=len(_coll(data, "frontingEntries")),
        tag_count=len(_coll(data, "tags")),
        custom_field_count=len(_coll(data, "customFields")),
        journal_count=len(journals),
        note_count=len(notes),
        board_message_count=len(board),
        poll_count=_count_polls(data),
        reminder_count=len(_coll(data, "reminders")),
        asset_count=len(_coll(data, "assets")),
    )
    summary.limit_warnings = il.import_row_cap_warnings(
        {
            "groups": summary.system_count,
            "tags": summary.tag_count,
            "custom_fields": summary.custom_field_count,
            "fronts": summary.front_history_count,
            "journal_entries": summary.journal_count + summary.note_count,
            "messages": summary.board_message_count,
            "polls": summary.poll_count,
        }
    )
    return summary


# --- Import -----------------------------------------------------------------


async def run_import(
    data: dict,
    options: AmpersandImportOptions,
    system: System,
    user: User,
    db: AsyncSession,
    stored_images: list[StoredImportImage],
) -> AmpersandImportResult:
    """Import an Ampersand export into the user's system.

    ``stored_images`` is a caller-owned accumulator of blobs written to
    storage; the runner scrubs it if the import fails, since storage
    writes don't roll back with the DB transaction.
    """
    result = AmpersandImportResult()
    warnings: list[str] = []
    report = ClampReport()
    dedupe = options.conflict_strategy != ImportConflictStrategy.CREATE

    images_enabled = options.images
    if options.images and not user_can_upload_images(user):
        images_enabled = False
        warnings.append(
            "Image restore skipped: image uploads are not enabled for this "
            "account. Members and journals imported without their avatars."
        )

    # --- Row caps (parse-bomb guard) -------------------------------------
    il.enforce_import_row_caps(
        {
            "groups": len(_coll(data, "systems")) if options.groups else 0,
            "tags": len(_coll(data, "tags")) if options.tags else 0,
            "custom_fields": (
                len(_coll(data, "customFields")) if options.custom_fields else 0
            ),
            "fronts": (
                len(_coll(data, "frontingEntries")) if options.front_history else 0
            ),
            "journal_entries": (
                (len(_coll(data, "journalPosts")) if options.journals else 0)
                + (len(_coll(data, "notes")) if options.notes else 0)
            ),
            "messages": (
                len(_coll(data, "boardMessages")) if options.board_messages else 0
            ),
            "polls": _count_polls(data) if options.board_messages else 0,
        }
    )

    # --- Members + custom fronts (built as candidates first) -------------
    amp_members = _coll(data, "members")
    if options.member_ids is not None:
        selected = set(options.member_ids)
        amp_members = [m for m in amp_members if m.get("uuid") in selected]
    if not options.custom_fronts:
        amp_members = [m for m in amp_members if not m.get("isCustomFront")]

    # Amp member uuid -> plaintext name, for journal author-name snapshots.
    amp_id_to_name: dict[str, str] = {}
    # Amp member uuid -> its Amp system uuid, for group membership wiring.
    amp_id_to_system: dict[str, str] = {}
    member_candidates: list[tuple[Member, dict]] = []
    for amp_m in amp_members:
        amp_id = _coerce_str(amp_m.get("uuid")) or ""
        plaintext_name = clamp_str(
            _coerce_str(amp_m.get("name")) or "unnamed", il.M_NAME, report=report
        )
        if amp_id:
            amp_id_to_name[amp_id] = plaintext_name
            sys_ref = _coerce_str(amp_m.get("system"))
            if sys_ref:
                amp_id_to_system[amp_id] = sys_ref
        plaintext_desc = _coerce_str(amp_m.get("description"))
        member = Member(
            id=uuid.uuid4(),
            system_id=system.id,
            name=encrypt(plaintext_name),
            name_hash=blind_index(plaintext_name),
            description=encrypt(plaintext_desc) if plaintext_desc is not None else None,
            pronouns=clamp_str(
                _coerce_str(amp_m.get("pronouns")) or None, il.M_PRONOUNS, report=report
            ),
            color=_normalize_color(amp_m.get("color")),
            is_custom_front=bool(amp_m.get("isCustomFront")),
        )
        created = _parse_iso(amp_m.get("dateCreated"))
        if created:
            member.created_at = created
        if amp_m.get("isArchived"):
            member.archived_at = _parse_iso(amp_m.get("dateCreated")) or datetime.now(UTC)
        member_candidates.append((member, amp_m))

    # Member cap: count only rows this run would CREATE, before any write.
    index = await load_member_match_index(db, system.id)
    new_count = count_new_members(
        [candidate_key(m) for m, _ in member_candidates],
        index=index,
        strategy=options.conflict_strategy,
    )
    await enforce_import_member_cap(db, system, new_count)

    # Resolve dispositions and add created rows.
    amp_id_to_member: dict[str, Member] = {}
    created_member_ids: set[uuid.UUID] = set()
    for member, amp_m in member_candidates:
        resolution = resolve_member(member, index=index, strategy=options.conflict_strategy)
        amp_id = _coerce_str(amp_m.get("uuid")) or ""
        if resolution.disposition == "created":
            db.add(resolution.member)
            created_member_ids.add(resolution.member.id)
            if resolution.member.is_custom_front:
                result.custom_fronts_imported += 1
            else:
                result.members_imported += 1
        elif resolution.disposition == "updated":
            result.members_updated += 1
        else:
            result.members_skipped += 1
        if amp_id:
            amp_id_to_member[amp_id] = resolution.member

    await db.flush()

    # Avatars: decode + store only for members we actually created, so a
    # deduped-skip doesn't leave an orphaned blob.
    if images_enabled:
        for member, amp_m in member_candidates:
            if member.id not in created_member_ids:
                continue
            raw = _decode_data_uri(amp_m.get("image"))
            if raw is None:
                continue
            stored = await _store_avatar(raw, db, user, warnings)
            if stored is not None:
                stored_images.append(stored)
                member.avatar_url = stored.key
                result.images_imported += 1

    # --- Systems -> groups -----------------------------------------------
    if options.groups:
        await _import_systems_as_groups(
            data, system, db, amp_id_to_member, amp_id_to_system,
            created_member_ids, result, warnings, report, dedupe,
        )

    # --- Tags (+ member.role) --------------------------------------------
    if options.tags:
        result.tags_imported += await _import_tags(
            data, system, db, amp_id_to_member, created_member_ids, warnings, report
        )

    # --- Custom fields (+ member.age) ------------------------------------
    if options.custom_fields:
        await _import_custom_fields(
            data, system, db, amp_id_to_member, created_member_ids, result, report
        )

    # --- Front history ----------------------------------------------------
    if options.front_history:
        await _import_fronts(data, system, db, amp_id_to_member, result, warnings, dedupe)

    # --- Journals + notes -------------------------------------------------
    if options.journals or options.notes:
        await _import_journals(
            data, system, db, user, amp_id_to_member, amp_id_to_name,
            options, images_enabled, stored_images, result, warnings, report,
        )

    # --- Board messages + polls ------------------------------------------
    if options.board_messages:
        await _import_board(
            data, system, db, amp_id_to_member, result, warnings, report
        )

    # --- Reminders (best-effort) -----------------------------------------
    if options.reminders:
        await _import_reminders(
            data, system, db, amp_id_to_member, result, warnings, report
        )

    result.warnings = warnings + report.to_warnings()
    return result


async def _store_avatar(
    raw: bytes, db: AsyncSession, user: User, warnings: list[str]
) -> StoredImportImage | None:
    """Store one decoded image as an avatar; None + a warning on rejection.

    A ``quota_full`` rejection is terminal for images (the quota won't
    free up mid-import), so it's surfaced once and further stores are the
    caller's to stop - but since avatars are per-member and small, we just
    warn and skip each."""
    try:
        return await store_imported_image(raw, db=db, user=user, purpose="avatar")
    except ImportImageError as exc:
        if exc.reason == "quota_full":
            warnings.append("Some images were skipped: storage quota reached.")
        elif exc.reason == "bad_format":
            warnings.append("An image was skipped (unsupported format).")
        else:
            warnings.append("An image was skipped (rejected by the image normaliser).")
        return None


async def _import_systems_as_groups(
    data: dict,
    system: System,
    db: AsyncSession,
    amp_id_to_member: dict[str, Member],
    amp_id_to_system: dict[str, str],
    created_member_ids: set[uuid.UUID],
    result: AmpersandImportResult,
    warnings: list[str],
    report: ClampReport,
    dedupe: bool,
) -> None:
    """Each Ampersand system -> a Sheaf group; ``parent`` -> ``parent_id``.

    Members join the group for their ``system`` FK. Group depth is clamped
    to MAX_GROUP_DEPTH, reusing the native importer's reparent helper.
    Group avatars don't exist, so system ``image`` is dropped."""
    amp_systems = _coll(data, "systems")
    if not amp_systems:
        return

    group_index = await load_group_index(db, system.id) if dedupe else ContentMatchIndex()
    amp_sysid_to_group: dict[str, Group] = {}
    created_group_ids: set[uuid.UUID] = set()
    dropped_images = 0

    # First pass: create groups (no parent links yet).
    for amp_s in amp_systems:
        amp_sid = _coerce_str(amp_s.get("uuid")) or ""
        name = clamp_str(
            _coerce_str(amp_s.get("name")) or "system", il.GROUP_NAME, report=report
        )
        existing = group_index.get(name) if dedupe else None
        if existing is not None:
            amp_sysid_to_group[amp_sid] = existing
            result.groups_skipped += 1
            continue
        group = Group(
            id=uuid.uuid4(),
            system_id=system.id,
            name=name,
            description=_coerce_str(amp_s.get("description")),
            color=_normalize_color(amp_s.get("color")),
        )
        db.add(group)
        group_index.register(name, group)
        created_group_ids.add(group.id)
        amp_sysid_to_group[amp_sid] = group
        result.groups_imported += 1
        if amp_s.get("image"):
            dropped_images += 1

    await db.flush()

    # Second pass: parent links (only on groups we created this run).
    unresolved_parents = 0
    for amp_s in amp_systems:
        amp_sid = _coerce_str(amp_s.get("uuid")) or ""
        group = amp_sysid_to_group.get(amp_sid)
        if group is None or group.id not in created_group_ids:
            continue
        parent_ref = _coerce_str(amp_s.get("parent"))
        if parent_ref:
            parent = amp_sysid_to_group.get(parent_ref)
            if parent is not None:
                group.parent_id = parent.id
            else:
                unresolved_parents += 1

    # Member -> group membership.
    unknown_members = 0
    seen_pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for amp_mid, sys_ref in amp_id_to_system.items():
        member = amp_id_to_member.get(amp_mid)
        group = amp_sysid_to_group.get(sys_ref)
        if member is None or group is None:
            if member is not None and group is None:
                unknown_members += 1
            continue
        # Only associate members created this run (a deduped-skip member is
        # already grouped as it was on the prior import).
        if member.id not in created_member_ids:
            continue
        pair = (group.id, member.id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        await db.execute(
            group_members.insert().values(group_id=group.id, member_id=member.id)
        )

    # Depth clamp, reusing the native importer's non-destructive reparent.
    if created_group_ids:
        from sheaf.api.v1.groups import MAX_GROUP_DEPTH
        from sheaf.services.sheaf_import import correct_nesting_depth

        await db.flush()
        rows = await db.execute(
            select(Group.id, Group.parent_id).where(Group.system_id == system.id)
        )
        parent_of = {gid: pid for gid, pid in rows.all()}
        correction = correct_nesting_depth(parent_of, max_depth=MAX_GROUP_DEPTH)
        moved = 0
        for group in amp_sysid_to_group.values():
            if group.id not in created_group_ids:
                continue
            if group.id in correction.moved or group.id in correction.cycle_broken:
                group.parent_id = correction.parent_of[group.id]
                moved += 1
        if moved:
            warnings.append(
                f"{moved} imported group(s) exceeded the maximum nesting depth "
                f"({MAX_GROUP_DEPTH}) or looped and were moved up to fit."
            )

    if dropped_images:
        warnings.append(
            f"Dropped {dropped_images} system image(s): Sheaf groups (which "
            "Ampersand systems import as) have no avatar."
        )
    if unresolved_parents:
        warnings.append(
            f"Dropped {unresolved_parents} system parent link(s) that pointed at "
            "a system not present in the export."
        )
    if unknown_members:
        warnings.append(
            f"{unknown_members} member(s) referenced a system not present in the "
            "export; they were imported without a group."
        )


async def _import_tags(
    data: dict,
    system: System,
    db: AsyncSession,
    amp_id_to_member: dict[str, Member],
    created_member_ids: set[uuid.UUID],
    warnings: list[str],
    report: ClampReport,
) -> int:
    """Import member-type tags + member.role, deduped by name (ci).

    Ampersand tags are typed (member/journal/asset); only member tags map.
    ``member.tags`` reference tag uuids; ``member.role`` is a free string
    that also becomes a tag (SimplyPlural precedent)."""
    existing = await db.execute(select(Tag).where(Tag.system_id == system.id))
    tag_by_name: dict[str, Tag] = {t.name.lower(): t for t in existing.scalars().all()}
    # Amp tag uuid -> Sheaf Tag, for member.tags[] references.
    amp_tagid_to_tag: dict[str, Tag] = {}
    created = 0

    def _get_or_create(name: str) -> Tag:
        nonlocal created
        clamped = clamp_str(name, il.TAG_NAME, report=report) or name[: il.TAG_NAME.limit]
        key = clamped.lower()
        tag = tag_by_name.get(key)
        if tag is None:
            tag = Tag(id=uuid.uuid4(), system_id=system.id, name=clamped)
            db.add(tag)
            tag_by_name[key] = tag
            created += 1
        return tag

    for amp_t in _coll(data, "tags"):
        if amp_t.get("type") != "member":
            continue
        name = _coerce_str(amp_t.get("name"))
        if not name:
            continue
        tag = _get_or_create(name)
        amp_tid = _coerce_str(amp_t.get("uuid"))
        if amp_tid:
            amp_tagid_to_tag[amp_tid] = tag

    await db.flush()

    # Associate members with their tags + role.
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for amp_m in _coll(data, "members"):
        amp_mid = _coerce_str(amp_m.get("uuid"))
        member = amp_id_to_member.get(amp_mid) if amp_mid else None
        if member is None or member.id not in created_member_ids:
            continue
        wanted: list[Tag] = []
        for tref in amp_m.get("tags", []):
            tag = amp_tagid_to_tag.get(_coerce_str(tref) or "")
            if tag is not None:
                wanted.append(tag)
        role = _coerce_str(amp_m.get("role"))
        if role and role.strip():
            wanted.append(_get_or_create(role.strip()))
        await db.flush()
        for tag in wanted:
            pair = (tag.id, member.id)
            if pair in seen:
                continue
            seen.add(pair)
            await db.execute(
                member_tags.insert().values(tag_id=tag.id, member_id=member.id)
            )
    return created


async def _import_custom_fields(
    data: dict,
    system: System,
    db: AsyncSession,
    amp_id_to_member: dict[str, Member],
    created_member_ids: set[uuid.UUID],
    result: AmpersandImportResult,
    report: ClampReport,
) -> None:
    """Import customField defs (all TEXT) + per-member values, plus a
    synthesised "Age" field from ``member.age``."""
    existing = await db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.system_id == system.id
        )
    )
    def_by_key: dict[tuple[str, str], CustomFieldDefinition] = {
        (d.name, d.field_type.value if hasattr(d.field_type, "value") else d.field_type): d
        for d in existing.scalars().all()
    }
    amp_fieldid_to_def: dict[str, CustomFieldDefinition] = {}

    def _get_or_create_field(name: str, order: int) -> CustomFieldDefinition:
        clamped = clamp_str(name, il.CF_NAME, report=report) or name[: il.CF_NAME.limit]
        key = (clamped, FieldType.TEXT.value)
        field_def = def_by_key.get(key)
        if field_def is None:
            field_def = CustomFieldDefinition(
                id=uuid.uuid4(),
                system_id=system.id,
                name=clamped,
                field_type=FieldType.TEXT,
                order=order,
            )
            db.add(field_def)
            def_by_key[key] = field_def
            result.custom_fields_imported += 1
        else:
            result.custom_fields_skipped += 1
        return field_def

    for idx, amp_f in enumerate(_coll(data, "customFields")):
        name = _coerce_str(amp_f.get("name")) or f"field_{idx}"
        field_def = _get_or_create_field(name, idx)
        amp_fid = _coerce_str(amp_f.get("uuid"))
        if amp_fid:
            amp_fieldid_to_def[amp_fid] = field_def

    # "Age" field, only if some member carries an age.
    age_field: CustomFieldDefinition | None = None
    if any(m.get("age") is not None for m in _coll(data, "members")):
        age_field = _get_or_create_field("Age", len(amp_fieldid_to_def))

    await db.flush()

    # Values. member.customFields is {fieldUuid: value}; member.age is a
    # number. Both keyed to the (field, member) UNIQUE, so guard the pair.
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for amp_m in _coll(data, "members"):
        amp_mid = _coerce_str(amp_m.get("uuid"))
        member = amp_id_to_member.get(amp_mid) if amp_mid else None
        if member is None or member.id not in created_member_ids:
            continue
        pending: list[tuple[CustomFieldDefinition, str]] = []
        cf = amp_m.get("customFields")
        if isinstance(cf, dict):
            for field_ref, raw_value in cf.items():
                if raw_value is None:
                    continue
                field_def = amp_fieldid_to_def.get(field_ref)
                if field_def is not None:
                    pending.append((field_def, str(raw_value)))
        age = amp_m.get("age")
        if age is not None and age_field is not None and not isinstance(age, bool):
            pending.append((age_field, str(age)))
        for field_def, value in pending:
            pair = (field_def.id, member.id)
            if pair in seen:
                continue
            seen.add(pair)
            db.add(
                CustomFieldValue(
                    id=uuid.uuid4(),
                    field_id=field_def.id,
                    member_id=member.id,
                    value=encrypt_field_value({"v": value}),
                )
            )


async def _import_fronts(
    data: dict,
    system: System,
    db: AsyncSession,
    amp_id_to_member: dict[str, Member],
    result: AmpersandImportResult,
    warnings: list[str],
    dedupe: bool,
) -> None:
    """Each frontingEntry -> one single-member Front. No coalescing."""
    front_index = await load_front_index(db, system.id) if dedupe else ContentMatchIndex()
    missing_member = 0
    bad_time = 0
    for amp_f in _coll(data, "frontingEntries"):
        member = amp_id_to_member.get(_coerce_str(amp_f.get("member")) or "")
        if member is None:
            missing_member += 1
            continue
        started = _parse_iso(amp_f.get("startTime"))
        if started is None:
            bad_time += 1
            continue
        ended = _parse_iso(amp_f.get("endTime"))
        started, ended, _swapped = normalize_front_interval(started, ended)

        if dedupe:
            fkey = front_key(started, ended, {member.id})
            if front_index.get(fkey) is not None:
                result.fronts_skipped += 1
                continue
            front_index.register(fkey)

        custom_status = _coerce_str(amp_f.get("customStatus"))
        front = Front(
            id=uuid.uuid4(),
            system_id=system.id,
            started_at=started,
            ended_at=ended,
            custom_status=encrypt(custom_status) if custom_status else None,
        )
        db.add(front)
        await db.flush()
        await db.execute(
            front_members.insert().values(front_id=front.id, member_id=member.id)
        )
        result.fronts_imported += 1
    if missing_member:
        warnings.append(
            f"Skipped {missing_member} fronting entries whose member wasn't "
            "imported."
        )
    if bad_time:
        warnings.append(
            f"Skipped {bad_time} fronting entries with an unparseable start time."
        )


async def _import_journals(
    data: dict,
    system: System,
    db: AsyncSession,
    user: User,
    amp_id_to_member: dict[str, Member],
    amp_id_to_name: dict[str, str],
    options: AmpersandImportOptions,
    images_enabled: bool,
    stored_images: list[StoredImportImage],
    result: AmpersandImportResult,
    warnings: list[str],
    report: ClampReport,
) -> None:
    """journalPosts -> JournalEntry; system notes -> system-wide entries.

    A single-member post also sets ``member_id``; multi/zero-member posts
    are system-wide with the author snapshot populated. ``subtitle`` and
    ``contentWarning`` fold into the body (Sheaf journals have neither
    field); ``cover`` is stored to ``image_keys``. Journal ``comments`` are
    dropped - Sheaf journals have no comment surface."""
    dropped_comments = 0

    if options.journals:
        for amp_j in _coll(data, "journalPosts"):
            member_objs = [
                amp_id_to_member[mid]
                for mid in (
                    _coerce_str(r) for r in amp_j.get("members", [])
                )
                if mid and mid in amp_id_to_member
            ]
            author_names = [
                amp_id_to_name[mid]
                for mid in (_coerce_str(r) for r in amp_j.get("members", []))
                if mid and mid in amp_id_to_name
            ]
            body_parts: list[str] = []
            cw = _coerce_str(amp_j.get("contentWarning"))
            if cw:
                body_parts.append(f"> Content warning: {cw}")
            subtitle = _coerce_str(amp_j.get("subtitle"))
            if subtitle:
                body_parts.append(f"*{subtitle}*")
            body_parts.append(_coerce_str(amp_j.get("body")) or "")
            body = "\n\n".join(p for p in body_parts if p)

            image_keys: list[str] = []
            if images_enabled:
                raw = _decode_data_uri(amp_j.get("cover"))
                if raw is not None:
                    stored = await _store_avatar(raw, db, user, warnings)
                    if stored is not None:
                        stored_images.append(stored)
                        image_keys.append(stored.key)
                        result.images_imported += 1

            j_title = _coerce_str(amp_j.get("title"))
            entry = JournalEntry(
                id=uuid.uuid4(),
                system_id=system.id,
                member_id=member_objs[0].id if len(member_objs) == 1 else None,
                title=(
                    encrypt(clamp_str(j_title, il.JOURNAL_TITLE, report=report))
                    if j_title
                    else None
                ),
                body=encrypt(body),
                visibility="system",
                author_user_id=system.user_id,
                author_member_ids=[str(m.id) for m in member_objs],
                author_member_names=author_names,
                image_keys=image_keys,
            )
            created = _parse_iso(amp_j.get("date"))
            if created:
                entry.created_at = created
            db.add(entry)
            result.journals_imported += 1
            if amp_j.get("comments"):
                dropped_comments += len(amp_j.get("comments") or [])

    if options.notes:
        for amp_n in _coll(data, "notes"):
            title = _coerce_str(amp_n.get("title"))
            content = _coerce_str(amp_n.get("content")) or ""
            entry = JournalEntry(
                id=uuid.uuid4(),
                system_id=system.id,
                member_id=None,
                title=(
                    encrypt(clamp_str(title, il.JOURNAL_TITLE, report=report))
                    if title
                    else None
                ),
                body=encrypt(content),
                visibility="system",
                author_user_id=system.user_id,
                author_member_ids=[],
                author_member_names=[],
                image_keys=[],
            )
            db.add(entry)
            result.notes_imported += 1

    if dropped_comments:
        warnings.append(
            f"Dropped {dropped_comments} journal comment(s): Sheaf journals have "
            "no comment surface."
        )


async def _import_board(
    data: dict,
    system: System,
    db: AsyncSession,
    amp_id_to_member: dict[str, Member],
    result: AmpersandImportResult,
    warnings: list[str],
    report: ClampReport,
) -> None:
    """boardMessages -> system-board Messages (+ comments as replies, polls).

    Ampersand board posts have a title AND body: the title is prepended as
    a bold line. ``members[0]`` is taken as the author. ``comments`` become
    single-level replies to the post (Sheaf boards are single-level; the
    inter-comment ``replyTo`` chain is flattened). Each post's ``poll``
    imports as a closed-window Poll with per-voter votes."""
    dropped_polls_no_options = 0
    for amp_b in _coll(data, "boardMessages"):
        members = [
            amp_id_to_member[mid]
            for mid in (_coerce_str(r) for r in amp_b.get("members", []))
            if mid and mid in amp_id_to_member
        ]
        author = members[0] if members else None
        title = _coerce_str(amp_b.get("title"))
        raw_body = _coerce_str(amp_b.get("body")) or ""
        body = f"**{title}**\n\n{raw_body}".strip() if title else raw_body
        message = Message(
            id=uuid.uuid4(),
            system_id=system.id,
            board_kind=BoardKind.SYSTEM.value,
            board_member_id=None,
            author_member_id=author.id if author else None,
            body=encrypt(clamp_str(body, il.MESSAGE_BODY, report=report) or ""),
        )
        created = _parse_iso(amp_b.get("date"))
        if created:
            message.created_at = created
        db.add(message)
        await db.flush()
        result.messages_imported += 1

        # Comments -> replies to this post.
        for amp_c in amp_b.get("comments") or []:
            if not isinstance(amp_c, dict):
                continue
            c_author = amp_id_to_member.get(_coerce_str(amp_c.get("member")) or "")
            c_body = _coerce_str(amp_c.get("comment")) or ""
            reply = Message(
                id=uuid.uuid4(),
                system_id=system.id,
                board_kind=BoardKind.SYSTEM.value,
                board_member_id=None,
                author_member_id=c_author.id if c_author else None,
                body=encrypt(clamp_str(c_body, il.MESSAGE_BODY, report=report) or ""),
                parent_message_id=message.id,
            )
            c_created = _parse_iso(amp_c.get("date"))
            if c_created:
                reply.created_at = c_created
            db.add(reply)
            result.messages_imported += 1

        # Poll.
        poll_data = amp_b.get("poll")
        if isinstance(poll_data, dict):
            if await _import_poll(poll_data, system, db, amp_id_to_member, report):
                result.polls_imported += 1
            else:
                dropped_polls_no_options += 1

    if dropped_polls_no_options:
        warnings.append(
            f"Dropped {dropped_polls_no_options} poll(s) that had no options."
        )


async def _import_poll(
    poll_data: dict,
    system: System,
    db: AsyncSession,
    amp_id_to_member: dict[str, Member],
    report: ClampReport,
) -> bool:
    """One Ampersand poll -> Poll + options + per-voter votes. Returns
    False (no import) when the poll has no options. Per-vote ``reason``
    freeform is dropped (Sheaf votes carry no freeform field)."""
    entries = poll_data.get("entries")
    if not isinstance(entries, list) or not entries:
        return False

    now = datetime.now(UTC)
    poll = Poll(
        id=uuid.uuid4(),
        system_id=system.id,
        question=encrypt(clamp_str("Imported poll", il.POLL_QUESTION, report=report)),
        description=None,
        kind=(
            PollKind.MULTI_CHOICE.value
            if poll_data.get("multipleChoice")
            else PollKind.SINGLE_CHOICE.value
        ),
        results_visibility=PollResultsVisibility.LIVE.value,
        closes_at=now + timedelta(days=_POLL_OPEN_DAYS),
        retention_days=_POLL_RETENTION_DAYS,
    )
    db.add(poll)
    await db.flush()

    # Options + invert per-option votes into per-voter option sets.
    member_to_options: dict[uuid.UUID, list[uuid.UUID]] = {}
    created_any = False
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        text = _coerce_str(entry.get("choice")) or f"Option {position + 1}"
        option = PollOption(
            id=uuid.uuid4(),
            poll_id=poll.id,
            text=encrypt(clamp_str(text, il.POLL_OPTION, report=report) or ""),
            position=position,
        )
        db.add(option)
        created_any = True
        for vote in entry.get("votes") or []:
            if not isinstance(vote, dict):
                continue
            voter = amp_id_to_member.get(_coerce_str(vote.get("member")) or "")
            if voter is None:
                continue
            member_to_options.setdefault(voter.id, []).append(option.id)

    if not created_any:
        return False
    await db.flush()

    for member_id, option_ids in member_to_options.items():
        db.add(
            PollVote(
                id=uuid.uuid4(),
                poll_id=poll.id,
                voted_as_member_id=member_id,
                option_ids=option_ids,
            )
        )
    return True


async def _import_reminders(
    data: dict,
    system: System,
    db: AsyncSession,
    amp_id_to_member: dict[str, Member],
    result: AmpersandImportResult,
    warnings: list[str],
    report: ClampReport,
) -> None:
    """Ampersand reminders -> automated (front-event) Sheaf reminders.

    Sheaf reminders require a notification channel; Ampersand exports carry
    none, so a single disabled placeholder channel is synthesised and all
    imported reminders bind to it. The user re-enables it and sets a real
    destination to make them fire. ``trigger`` fronting/fronted ->
    start/stop; ``members[]`` fan out one reminder per member via
    ``trigger_member_id`` (automated reminders trigger on a single member)."""
    reminders = _coll(data, "reminders")
    if not reminders:
        return

    channel = await _synthesise_reminder_channel(system, db)
    made = 0
    for amp_r in reminders:
        title = _coerce_str(amp_r.get("title")) or "reminder"
        message_body = _coerce_str(amp_r.get("message"))
        trigger = _coerce_str(amp_r.get("trigger"))
        trigger_event = "stop" if trigger == "fronted" else "start"
        delay = amp_r.get("delay")
        delay_seconds = (
            int(delay) // 1000
            if isinstance(delay, (int, float)) and not isinstance(delay, bool)
            else None
        )
        enabled = bool(amp_r.get("active", True))

        member_refs = [
            amp_id_to_member[mid]
            for mid in (_coerce_str(r) for r in amp_r.get("members", []) or [])
            if mid and mid in amp_id_to_member
        ]
        # Fan out one automated reminder per targeted member; none -> a
        # single "any member" reminder.
        targets: list[Member | None] = member_refs if member_refs else [None]
        for target in targets:
            reminder = Reminder(
                id=uuid.uuid4(),
                system_id=system.id,
                channel_id=channel.id,
                name=clamp_str(title, il.REMINDER_NAME, report=report),
                title=encrypt(clamp_str(title, il.REMINDER_TITLE, report=report) or ""),
                body=(
                    encrypt(clamp_str(message_body, il.REMINDER_BODY, report=report))
                    if message_body
                    else None
                ),
                enabled=enabled,
                trigger_type="automated",
                trigger_member_id=target.id if target else None,
                trigger_event=trigger_event,
                delay_seconds=delay_seconds,
                scope="system",
            )
            db.add(reminder)
            made += 1

    result.reminders_imported += made
    warnings.append(
        f"Imported {made} reminder(s) bound to a disabled placeholder "
        "notification channel ('Imported from Ampersand'). Set a real "
        "destination and enable the channel to make them fire."
    )


async def _synthesise_reminder_channel(
    system: System, db: AsyncSession
) -> NotificationChannel:
    """A single disabled webhook channel to satisfy the reminder FK.

    Disabled + owner-paused so it never dispatches against its empty
    config; the user configures a real destination post-import."""
    token = WatchToken(
        id=uuid.uuid4(),
        system_id=system.id,
        label="Imported from Ampersand",
    )
    db.add(token)
    await db.flush()
    channel = NotificationChannel(
        id=uuid.uuid4(),
        watch_token_id=token.id,
        name="Imported from Ampersand",
        destination_type=DestinationType.WEBHOOK.value,
        destination_config={},
        destination_state=DestinationState.DISABLED.value,
        paused_by_sender=True,
    )
    db.add(channel)
    await db.flush()
    return channel

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

import base64
import binascii
import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import blind_index, encrypt
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue, FieldType
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.member import Member, front_members, group_members
from sheaf.models.message import BoardKind, Message
from sheaf.models.system import System
from sheaf.schemas.sp_import import (
    SPImportOptions,
    SPImportResult,
    SPPreviewCustomFront,
    SPPreviewMember,
    SPPreviewSummary,
)
from sheaf.services import import_limits as il
from sheaf.services.custom_fields import encrypt_field_value
from sheaf.services.import_content_dedup import (
    ContentMatchIndex,
    CountedIndex,
    PairGuard,
    front_key,
    load_field_def_index,
    load_field_value_guard,
    load_front_index,
    load_group_index,
    load_group_member_guard,
    load_message_count_index,
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
from sheaf.services.import_parsing import sanitize_external_avatar_url
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
    """Return an SP collection as a list of dict rows.

    SP exports a collection as either a JSON array or a map keyed by id
    (`{"id1": {...}, "id2": {...}}`) - the shape drifted across export versions.
    Normalise both to a list and drop any non-dict entries, so the per-record
    walks can iterate uniformly without crashing on a map-keyed collection (which
    would otherwise iterate the string keys). Missing -> []."""
    raw = data.get(name)
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        return [r for r in raw.values() if isinstance(r, dict)]
    return []


def _get_collection_alt(data: dict, *names: str) -> list[dict]:
    """First non-empty collection across alternate key names.

    SP renamed several collections across versions (e.g. `frontStatuses` ->
    `customFronts`); try each in order and return the first that has rows."""
    for name in names:
        coll = _get_collection(data, name)
        if coll:
            return coll
    return []


def _coerce_str(value: object) -> str | None:
    """Return a plain string, or None for null / non-string values.

    SP exports come out of MongoDB, where a field the schema treats as a string
    is occasionally null, a number, or an object in real data. Coerce defensively
    so one malformed field can't crash slicing or `encrypt()`. A non-string value
    is dropped, never str()-quoted, so member content can't leak into an error."""
    return value if isinstance(value, str) else None


def _epoch_ms_to_datetime(ms: int | float) -> datetime | None:
    """Millisecond epoch -> aware UTC datetime, or None if out of range."""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_iso_utc(value: str) -> datetime | None:
    """Parse an ISO-8601 string; SP often omits the zone, so treat naive as UTC."""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_sp_time(value: object) -> datetime | None:
    """Parse an SP timestamp across every shape real exports carry.

    SP timestamps come as: an integer/float epoch in milliseconds, a numeric
    string, an ISO-8601 string (frequently zone-less), or a Firebase-style object
    (`{_seconds, _nanoseconds}` or `{seconds, nanoseconds}`). Missing, malformed,
    or out-of-range values return None so one bad row can't crash or skew the
    import. Replaces the int-only converter that silently dropped (or, before
    hardening, crashed on) the string and Firebase shapes live exports actually
    use - a prime cause of "every front got skipped" imports.

    Not handled: the rare `{__time__: ...}` Firebase shape (Prism doesn't either);
    add it here if a real export turns one up."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _epoch_ms_to_datetime(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return _epoch_ms_to_datetime(int(s))
        except ValueError:
            return _parse_iso_utc(s)
    if isinstance(value, dict):
        seconds = value.get("_seconds", value.get("seconds"))
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
            raw_nanos = value.get("_nanoseconds", value.get("nanoseconds"))
            nanos = (
                raw_nanos
                if isinstance(raw_nanos, (int, float))
                and not isinstance(raw_nanos, bool)
                else 0
            )
            return _epoch_ms_to_datetime(
                int(seconds * 1000) + int(nanos // 1_000_000)
            )
    return None


# --- Chat-message helpers --------------------------------------------------

_SP_B64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_SP_MENTION_RE = re.compile(r"<###@([^>#]+?)###>")
# A short alphanumeric body (e.g. "test") is valid base64 but obviously plain;
# SP also leaves a stale iv around after decrypting, so this dodges that false
# positive.
_SP_SHORT_PLAIN_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")


def _strict_b64decode(value: str) -> bytes | None:
    """Decode strict base64, or None. Rejects whitespace, bad padding, and any
    non-base64 character so ordinary prose isn't mistaken for ciphertext."""
    if not value or value != value.strip() or len(value) % 4 != 0:
        return None
    if not _SP_B64_RE.match(value):
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _looks_like_plaintext(data: bytes) -> bool:
    """True if bytes decode as UTF-8 that is >=85% printable - i.e. never
    ciphertext, just base64-looking text."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not text:
        return False
    printable = sum(
        1 for ch in text if ch in "\t\n\r" or (ord(ch) >= 0x20 and ord(ch) != 0x7F)
    )
    return printable / len(text) >= 0.85


def _looks_encrypted_sp_message(content: str, raw_iv: object) -> bool:
    """Detect a still-encrypted legacy SP chat message (heuristic ported from
    Prism's importer).

    SP's old chat encryption stored a 16-byte IV alongside base64 ciphertext.
    The format is undocumented and we don't have the key, so callers DETECT and
    skip rather than decrypt. A message looks encrypted iff: an `iv` base64-
    decodes to exactly 16 bytes, the content isn't a short plain token, and the
    content base64-decodes to bytes that don't look like UTF-8 text."""
    if raw_iv is None:
        return False
    iv_bytes = _strict_b64decode(str(raw_iv))
    if iv_bytes is None or len(iv_bytes) != 16:
        return False
    if _SP_SHORT_PLAIN_RE.match(content):
        return False
    content_bytes = _strict_b64decode(content)
    if not content_bytes:
        return False
    return not _looks_like_plaintext(content_bytes)


def _rewrite_sp_mentions(text: str, sp_id_to_name: dict[str, str]) -> str:
    """Rewrite SP mention tokens `<###@spId###>` to `@name`. Unresolvable ids are
    left verbatim rather than dropped, so partial exports aren't truncated."""
    if "<###@" not in text:
        return text

    def repl(match: re.Match) -> str:
        name = sp_id_to_name.get(match.group(1))
        return f"@{name}" if name else match.group(0)

    return _SP_MENTION_RE.sub(repl, text)


def _sp_chat_rows(data: dict) -> list[tuple[str, dict]]:
    """Flatten SP chat into (channel_id, message) pairs across both export
    shapes: `messages` as a map of channelId -> [msg] (or a flat list), and
    `chatMessages` as a flat array where each message carries its own
    `channel`."""
    rows: list[tuple[str, dict]] = []
    raw = data.get("messages")
    if isinstance(raw, dict):
        for cid, msgs in raw.items():
            if isinstance(msgs, list):
                rows.extend(
                    (str(cid), m) for m in msgs if isinstance(m, dict)
                )
    elif isinstance(raw, list):
        rows.extend(
            (_coerce_str(m.get("channel")) or "", m)
            for m in raw
            if isinstance(m, dict)
        )
    for m in _get_collection(data, "chatMessages"):
        rows.append((_coerce_str(m.get("channel")) or "", m))
    return rows


def measure_sp_payload(data: dict, report: ClampReport) -> None:
    """Tally which SP fields exceed the schema caps, into ``report`` - the
    warn-before-import prediction.

    Walks the same SP keys ``run_import`` clamps, with the same caps, so the
    preview's warnings match what the import would shorten. Only string values
    are measured (guarded by ``_coerce_str``) so a malformed upload can't raise.
    SP custom fields only ever map to TEXT/DATE types, never a SELECT with a
    choice list, so there is no choice-count cap to measure here (unlike the
    native importer's ``custom_fields[].options.choices``)."""

    def s(value: object, cap: il.Cap) -> None:
        text = _coerce_str(value)
        if text is not None:
            clamp_str(text, cap, report=report)

    # System name: settings.systemName (newer) or users[0].username/name (older).
    sp_settings = data.get("settings")
    sp_settings = sp_settings if isinstance(sp_settings, dict) else {}
    sp_users = _get_collection(data, "users")
    sp_user = sp_users[0] if sp_users else {}
    s(
        sp_settings.get("systemName")
        or sp_user.get("username")
        or sp_user.get("name"),
        il.SYS_NAME,
    )

    for m in _get_collection(data, "members"):
        s(m.get("name"), il.M_NAME)
        s(m.get("displayName"), il.M_DISPLAY_NAME)
        s(m.get("pronouns"), il.M_PRONOUNS)

    for cf in _get_collection_alt(data, "frontStatuses", "customFronts"):
        s(cf.get("name"), il.M_NAME)

    for f in _get_collection(data, "customFields"):
        s(f.get("name"), il.CF_NAME)

    for g in _get_collection(data, "groups"):
        s(g.get("name"), il.GROUP_NAME)


def preview(data: dict) -> SPPreviewSummary:
    """Parse SP export JSON and return a summary for the user to review."""
    members = _get_collection(data, "members")
    custom_fronts = _get_collection_alt(data, "frontStatuses", "customFronts")
    fronts = _get_collection_alt(data, "frontHistory", "fronters")
    groups = _get_collection(data, "groups")
    fields = _get_collection(data, "customFields")
    notes = _get_collection(data, "notes")

    # System profile from users collection
    users = _get_collection(data, "users")
    system_name = _coerce_str(users[0].get("username")) if users else None

    summary = SPPreviewSummary(
        system_name=system_name,
        member_count=len(members),
        members=[
            SPPreviewMember(
                id=m.get("_id", ""), name=_coerce_str(m.get("name")) or "unnamed"
            )
            for m in members
        ],
        custom_front_count=len(custom_fronts),
        custom_fronts=[
            SPPreviewCustomFront(
                id=cf.get("_id", ""), name=_coerce_str(cf.get("name")) or "unnamed"
            )
            for cf in custom_fronts
        ],
        front_history_count=len(fronts),
        group_count=len(groups),
        custom_field_count=len(fields),
        note_count=len(notes),
        message_count=len(_sp_chat_rows(data)),
    )

    # Predict which over-cap fields the real import would shorten, so the user
    # sees the warning before committing. Same caps as run_import.
    report = ClampReport()
    measure_sp_payload(data, report)
    summary.limit_warnings = report.to_warnings()
    return summary


async def run_import(
    data: dict,
    options: SPImportOptions,
    system: System,
    db: AsyncSession,
) -> SPImportResult:
    """Import SP export data into the user's system."""
    result = SPImportResult()
    warnings: list[str] = []
    # Tally every cap a user-content write actually hit so the same numbers the
    # preview predicted land in the job's warning log. SP's String(N) columns
    # (display_name, pronouns, names) reject overflow at the DB, so these clamps
    # are a correctness backstop as well as a courtesy.
    report = ClampReport()

    # SP system-owner id, used to construct member / custom-front avatar URLs
    # from an avatarUuid (their avatars are served from the owner's namespace).
    # Resolved up front so it's available whether or not the profile runs.
    sp_users = _get_collection(data, "users")
    sp_owner_id = ""
    if sp_users:
        sp_owner_id = (
            _coerce_str(sp_users[0].get("uid"))
            or _coerce_str(sp_users[0].get("_id"))
            or ""
        )

    # --- System profile ---
    if options.system_profile:
        sp_user = sp_users[0] if sp_users else {}
        sp_settings = data.get("settings")
        sp_settings = sp_settings if isinstance(sp_settings, dict) else {}
        # System name: settings.systemName (newer) or users[0].username (older).
        sp_name = (
            _coerce_str(sp_settings.get("systemName"))
            or _coerce_str(sp_user.get("username"))
            or _coerce_str(sp_user.get("name"))
        )
        if sp_name and not system.name:
            system.name = clamp_str(sp_name, il.SYS_NAME, report=report)
        sp_desc = _coerce_str(sp_user.get("desc")) or _coerce_str(
            sp_settings.get("desc")
        )
        if sp_desc:
            system.description = sp_desc
        system.color = _normalize_color(sp_user.get("color")) or system.color
        sys_avatar = _sp_avatar_url(sp_user, sp_owner_id)
        if sys_avatar:
            system.avatar_url = sys_avatar

    # --- Members ---
    sp_members = _get_collection(data, "members")
    # Filter to selected members if specified
    if options.member_ids is not None:
        selected = set(options.member_ids)
        sp_members = [m for m in sp_members if m.get("_id") in selected]

    # Build member + custom-front candidates first (no DB writes), so the
    # member-cap check below counts only the rows this run would CREATE.
    # Both members and custom fronts are Member rows and count toward the
    # cap; under skip/update a pure re-import adds nothing.
    member_candidates: list[tuple[Member, str]] = []
    # SP id -> plaintext name, used to rewrite chat mention tokens later.
    sp_id_to_name: dict[str, str] = {}
    for sp_m in sp_members:
        sp_id = sp_m.get("_id", "")
        plaintext_name = clamp_str(
            _coerce_str(sp_m.get("name")) or "unnamed", il.M_NAME, report=report
        )
        if sp_id:
            sp_id_to_name[sp_id] = plaintext_name
        plaintext_description = _coerce_str(sp_m.get("desc"))
        member = Member(
            id=uuid.uuid4(),
            system_id=system.id,
            name=encrypt(plaintext_name),
            name_hash=blind_index(plaintext_name),
            display_name=clamp_str(
                _coerce_str(sp_m.get("displayName")) or None,
                il.M_DISPLAY_NAME,
                report=report,
            ),
            description=(
                encrypt(plaintext_description)
                if plaintext_description is not None
                else None
            ),
            pronouns=clamp_str(
                _coerce_str(sp_m.get("pronouns")) or None,
                il.M_PRONOUNS,
                report=report,
            ),
            avatar_url=_sp_avatar_url(sp_m, sp_owner_id),
            color=_normalize_color(sp_m.get("color")),
            privacy=_map_privacy(sp_m.get("private", True)),
        )
        member_candidates.append((member, sp_id))

    # --- Custom fronts → imported as Members with is_custom_front=True ---
    # SP's "frontStatuses" are non-counting fronting entities like "Asleep"
    # or "Away". Sheaf models them as Members carrying the is_custom_front
    # flag, which the UI uses to list them separately from headcounted
    # members and exclude them from member-count statistics.
    custom_front_candidates: list[tuple[Member, str]] = []
    if options.custom_fronts:
        for sp_cf in _get_collection_alt(data, "frontStatuses", "customFronts"):
            sp_id = sp_cf.get("_id", "")
            plaintext_cf_name = clamp_str(
                _coerce_str(sp_cf.get("name")) or "unnamed", il.M_NAME, report=report
            )
            if sp_id:
                sp_id_to_name[sp_id] = plaintext_cf_name
            plaintext_cf_description = _coerce_str(sp_cf.get("desc"))
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
                avatar_url=_sp_avatar_url(sp_cf, sp_owner_id),
                privacy=_map_privacy(sp_cf.get("private", True)),
                is_custom_front=True,
            )
            custom_front_candidates.append((member, sp_id))

    # Match against existing roster, then hard-fail before writing anything
    # if the NEW rows would blow the cap.
    index = await load_member_match_index(db, system.id)
    new_count = count_new_members(
        [
            candidate_key(m)
            for m, _ in (*member_candidates, *custom_front_candidates)
        ],
        index=index,
        strategy=options.conflict_strategy,
    )
    await enforce_import_member_cap(db, system, new_count)

    # Map SP member _id → resolved Sheaf Member for cross-referencing.
    # The map points at the resolved row (created / skipped / updated) so
    # later sections (fronts, custom fields, groups) link correctly.
    sp_id_to_member: dict[str, Member] = {}
    for member, sp_id in member_candidates:
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
        sp_id_to_member[sp_id] = resolution.member

    sp_id_to_custom_front: dict[str, Member] = {}
    for member, sp_id in custom_front_candidates:
        resolution = resolve_member(
            member, index=index, strategy=options.conflict_strategy
        )
        if resolution.disposition == "created":
            db.add(resolution.member)
            result.custom_fronts_imported += 1
        elif resolution.disposition == "updated":
            result.members_updated += 1
        else:
            result.members_skipped += 1
        sp_id_to_custom_front[sp_id] = resolution.member

    # Flush to get member IDs assigned
    await db.flush()

    # Combined lookup for front history references
    all_sp_to_member = {**sp_id_to_member, **sp_id_to_custom_front}

    dedupe = options.conflict_strategy != ImportConflictStrategy.CREATE

    # --- Custom fields ---
    sp_field_id_to_def: dict[str, CustomFieldDefinition] = {}
    if options.custom_fields:
        # Field definitions dedupe by (name, type) unconditionally,
        # matching the native importer: a re-import must not litter the
        # field list with a second copy of every column.
        field_index = await load_field_def_index(db, system.id)
        for idx, sp_f in enumerate(_get_collection(data, "customFields")):
            sp_fid = sp_f.get("_id", "")
            sp_type = sp_f.get("type", 0)
            sheaf_type = _SP_FIELD_TYPE_MAP.get(sp_type, FieldType.TEXT)
            name = clamp_str(
                _coerce_str(sp_f.get("name")) or f"field_{idx}",
                il.CF_NAME,
                report=report,
            )
            key = (name, sheaf_type.value)

            field_def = field_index.get(key)
            if field_def is None:
                field_def = CustomFieldDefinition(
                    id=uuid.uuid4(),
                    system_id=system.id,
                    name=name,
                    field_type=sheaf_type,
                    order=idx,
                )
                db.add(field_def)
                field_index.register(key, field_def)
                result.custom_fields_imported += 1
            else:
                result.custom_fields_skipped += 1
            sp_field_id_to_def[sp_fid] = field_def

        await db.flush()

        # Now import field values from member info maps. Index sp_members
        # by _id once to avoid O(n*m) lookups on systems with thousands
        # of members. The (field, member) pair guard is unconditional: a
        # reused definition plus a deduped member would otherwise trip
        # the UNIQUE(field_id, member_id) constraint - a hard error on
        # every re-import with custom fields.
        value_guard = await load_field_value_guard(db, system.id)
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
                if not value_guard.add((field_def.id, member.id)):
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
        group_index = (
            await load_group_index(db, system.id) if dedupe else ContentMatchIndex()
        )
        created_group_ids: set[uuid.UUID] = set()

        # First pass: create groups without parent links
        for sp_g in sp_groups:
            sp_gid = sp_g.get("_id", "")
            name = clamp_str(
                _coerce_str(sp_g.get("name")) or "unnamed", il.GROUP_NAME, report=report
            )
            existing_group = group_index.get(name) if dedupe else None
            if existing_group is not None:
                sp_gid_to_group[sp_gid] = existing_group
                result.groups_skipped += 1
                continue
            group = Group(
                id=uuid.uuid4(),
                system_id=system.id,
                name=name,
                description=sp_g.get("desc"),
                color=_normalize_color(sp_g.get("color")),
            )
            db.add(group)
            group_index.register(name, group)
            created_group_ids.add(group.id)
            sp_gid_to_group[sp_gid] = group
            result.groups_imported += 1

        await db.flush()

        # Second pass: wire parent links and member associations. Parent
        # links are only written onto groups created THIS run; the pair
        # guard covers reused group + skipped member and in-file dupes.
        group_member_guard = (
            await load_group_member_guard(db, system.id) if dedupe else PairGuard()
        )
        unknown_group_members = 0
        unresolvable_parents = 0
        for sp_g in sp_groups:
            sp_gid = sp_g.get("_id", "")
            group = sp_gid_to_group.get(sp_gid)
            if not group:
                continue

            # Parent
            sp_parent = sp_g.get("parent")
            if group.id in created_group_ids and sp_parent and sp_parent != "root":
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
                if not group_member_guard.add((group.id, member.id)):
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
        front_index = (
            await load_front_index(db, system.id) if dedupe else ContentMatchIndex()
        )
        fronts_missing_member = 0
        fronts_missing_member_ref = 0
        fronts_bad_timestamp = 0
        # Live/current fronts live under `fronters` in some exports; fall back to
        # it when `frontHistory` is absent (rows carry member + startTime, no
        # endTime, so they map to open fronts).
        fronts_swapped = 0
        for sp_f in _get_collection_alt(data, "frontHistory", "fronters"):
            sp_member_id = sp_f.get("member")
            if not sp_member_id:
                fronts_missing_member_ref += 1
                continue

            member = all_sp_to_member.get(sp_member_id)
            if not member:
                # Front references a member/custom front that wasn't imported
                fronts_missing_member += 1
                continue

            started = _parse_sp_time(sp_f.get("startTime"))
            if not started:
                fronts_bad_timestamp += 1
                continue

            ended = _parse_sp_time(sp_f.get("endTime"))
            is_live = sp_f.get("live", False)
            if is_live:
                ended = None

            started, ended, swapped = normalize_front_interval(started, ended)
            if swapped:
                fronts_swapped += 1

            if dedupe:
                fkey = front_key(started, ended, {member.id})
                if front_index.get(fkey) is not None:
                    result.fronts_skipped += 1
                    continue
                front_index.register(fkey)

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
        if fronts_bad_timestamp:
            warnings.append(
                f"Skipped {fronts_bad_timestamp} front-history rows with a "
                "missing or unparseable startTime."
            )
        if fronts_swapped:
            warnings.append(
                f"Adjusted {fronts_swapped} front-history "
                f"{'entry' if fronts_swapped == 1 else 'entries'} whose end "
                "time was before the start time (swapped the two)."
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

    # --- Chat messages -> system board ---
    if options.messages:
        m_imported, m_skipped, m_encrypted = await _import_messages(
            data, all_sp_to_member, sp_id_to_name, system, db, warnings, dedupe=dedupe
        )
        result.messages_imported = m_imported
        result.messages_skipped = m_skipped
        result.messages_encrypted_skipped = m_encrypted

    result.warnings = warnings + report.to_warnings()
    return result


async def _import_messages(
    data: dict,
    all_sp_to_member: dict[str, Member],
    sp_id_to_name: dict[str, str],
    system: System,
    db: AsyncSession,
    warnings: list[str],
    *,
    dedupe: bool = False,
) -> tuple[int, int, int]:
    """Collapse SP chat onto the Sheaf system board.

    Sheaf has one system board, not a channel set, so every message lands there,
    prefixed `[<channel>]` when more than one channel is present (matching the
    PluralSpace importer). Authors resolve via the SP member-id map; the reply
    chain is rebuilt in a second pass; mention tokens are rewritten.

    Legacy exports leave some bodies encrypted in an undocumented format; those
    are detected and skipped (we can't decrypt them) with a single warning.
    Returns (imported, skipped, encrypted_skipped)."""
    rows = _sp_chat_rows(data)
    if not rows:
        return 0, 0, 0

    channel_names: dict[str, str] = {}
    for ch in _get_collection(data, "channels"):
        cid = _coerce_str(ch.get("_id")) or _coerce_str(ch.get("id"))
        if cid:
            channel_names[cid] = _coerce_str(ch.get("name")) or "channel"
    multi_channel = len({cid for cid, _ in rows if cid}) > 1

    msg_index = (
        await load_message_count_index(db, system.id) if dedupe else CountedIndex()
    )

    imported = 0
    skipped = 0
    encrypted_skipped = 0
    missing_authors = 0
    sp_msg_to_new: dict[str, Message] = {}
    pending_replies: list[tuple[Message, str]] = []

    for channel_id, m in rows:
        content = (
            _coerce_str(m.get("message")) or _coerce_str(m.get("content")) or ""
        )
        if _looks_encrypted_sp_message(content, m.get("iv")):
            encrypted_skipped += 1
            continue
        created = _parse_sp_time(
            m.get("timestamp") or m.get("writtenAt") or m.get("createdAt")
        )
        if dedupe and created is not None and msg_index.should_skip((None, created)):
            skipped += 1
            continue
        body = _rewrite_sp_mentions(content, sp_id_to_name)
        if multi_channel:
            name = channel_names.get(channel_id, "channel")
            body = f"[{name}] {body}".rstrip()
        sender = (
            _coerce_str(m.get("sender"))
            or _coerce_str(m.get("writer"))
            or _coerce_str(m.get("member"))
        )
        author = all_sp_to_member.get(sender) if sender else None
        if sender and author is None:
            missing_authors += 1
        message = Message(
            id=uuid.uuid4(),
            system_id=system.id,
            board_kind=BoardKind.SYSTEM.value,
            board_member_id=None,
            author_member_id=author.id if author else None,
            body=encrypt(body),
        )
        if created:
            message.created_at = created
        db.add(message)
        sp_id = _coerce_str(m.get("_id")) or _coerce_str(m.get("id"))
        if sp_id:
            sp_msg_to_new[sp_id] = message
        reply_to = _coerce_str(m.get("replyTo"))
        if reply_to:
            pending_replies.append((message, reply_to))
        imported += 1

    await db.flush()

    # Second pass: wire reply pointers now every message exists. A reply whose
    # parent didn't import (encrypted, deleted, dedup-skipped) stays parentless.
    for message, reply_to in pending_replies:
        parent = sp_msg_to_new.get(reply_to)
        if parent is not None:
            message.parent_message_id = parent.id

    if encrypted_skipped:
        warnings.append(
            f"Skipped {encrypted_skipped} chat messages still encrypted in "
            "SimplyPlural's legacy format - Sheaf can't decrypt them. Request a "
            "fresh export, or import via the SP API, to bring readable chat across."
        )
    if missing_authors:
        warnings.append(
            f"{missing_authors} chat messages referenced an author not imported; "
            "those were attributed to nobody."
        )
    return imported, skipped, encrypted_skipped


def _normalize_color(color: object) -> str | None:
    """Normalize a colour to '#rrggbb', or None.

    Handles the shapes SP carries: 6-hex (with or without '#'), 3-hex shorthand,
    and 8-hex ARGB (alpha-first, as SP/Android colours store it) which is reduced
    to RGB by dropping the leading alpha byte. Non-string or unrecognised input
    returns None rather than a mangled value."""
    if not isinstance(color, str):
        return None
    s = color.strip().lstrip("#")
    if len(s) == 3:
        s = f"{s[0] * 2}{s[1] * 2}{s[2] * 2}"
    elif len(s) == 8:
        # ARGB -> RGB: drop the leading 2 alpha chars (alpha is the high byte).
        s = s[2:]
    if len(s) != 6 or not all(c in "0123456789abcdefABCDEF" for c in s):
        return None
    return f"#{s.lower()}"


_SP_AVATAR_BASE = "https://serve.apparyllis.com/avatars"


def _sp_avatar_url(obj: dict, owner_id: str | None) -> str | None:
    """Resolve an SP avatar to a policy-gated external URL.

    Prefer a direct `avatarUrl`; otherwise construct from `avatarUuid` plus the
    owning system id (the object's own `uid`, falling back to the passed
    `owner_id`), the way SP serves uploaded avatars. Everything routes through
    `sanitize_external_avatar_url` so the hotlink policy and scheme checks apply
    uniformly - a constructed URL is dropped just like a direct one when the
    instance forbids external images."""
    direct = sanitize_external_avatar_url(obj.get("avatarUrl"))
    if direct:
        return direct
    avatar_uuid = _coerce_str(obj.get("avatarUuid"))
    owner = _coerce_str(obj.get("uid")) or _coerce_str(owner_id)
    if avatar_uuid and owner:
        return sanitize_external_avatar_url(
            f"{_SP_AVATAR_BASE}/{owner}/{avatar_uuid}"
        )
    return None


def _map_privacy(private: object) -> str:
    """Map SP's boolean privacy to our enum value. Non-bool / missing -> private."""
    from sheaf.models.system import PrivacyLevel
    return PrivacyLevel.PUBLIC if private is False else PrivacyLevel.PRIVATE

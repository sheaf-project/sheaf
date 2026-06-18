"""Sheaf data import service.

Imports data from Sheaf's own export format. Versions "1" and "2" are
accepted. The importer round-trips the full export: system profile +
preferences, members, fronts, groups, tags, custom fields (+ values),
journals, content revisions, messages, polls, reminders, and the
notification config (watch tokens + channels + rules). Per-instance
runtime state that can't survive a move to a fresh instance is omitted
by the exporter (push subscriptions, activation hashes, webhook
secrets, last-fired/last-delivered timestamps, pending queues) and so
isn't consumed here either.

Generates new UUIDs for all imported entities and maps old IDs to new
ones for cross-references inside the file. References to user accounts
(journal/revision authorship) are re-pointed at the importing user;
historical audit actors are dropped, since the original account UUIDs
are meaningless on the target instance.

Re-import is idempotent. Members dedupe against the target roster (see
`import_dedup`): the chosen conflict strategy decides whether a member
that already exists is skipped (default) or updated. Everything else
dedupes by exact match (see `import_content_dedup`): tags and groups by
name, fronts by interval + member set, journals / revisions / messages /
polls / reminders / notification config by their preserved source
timestamps. Restoring a backup into a populated system therefore adds
only what is genuinely new; `conflict_strategy=create` restores the old
append-everything behaviour.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import blind_index, encrypt
from sheaf.models.content_revision import ContentRevision, ContentRevisionTarget
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue, FieldType
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.journal_entry import JournalEntry
from sheaf.models.member import Member, front_members, group_members, member_tags
from sheaf.models.message import Message
from sheaf.models.notification_channel import (
    CofrontRedaction,
    DestinationType,
    NotificationChannel,
    PayloadSensitivity,
)
from sheaf.models.notification_channel_group_rule import (
    GroupRuleAction,
    IncludePrivate,
    NotificationChannelGroupRule,
)
from sheaf.models.notification_channel_member_rule import NotificationChannelMemberRule
from sheaf.models.poll import (
    Poll,
    PollKind,
    PollOption,
    PollResultsVisibility,
    PollVote,
    PollVoteAction,
    PollVoteEvent,
)
from sheaf.models.reminder import Reminder, reminder_scope_members
from sheaf.models.system import DateFormat, PrivacyLevel, System
from sheaf.models.tag import Tag
from sheaf.models.watch_token import WatchToken
from sheaf.services.custom_fields import encrypt_field_value
from sheaf.services.import_content_dedup import (
    ContentMatchIndex,
    CountedIndex,
    PairGuard,
    front_key,
    load_channel_index,
    load_field_def_index,
    load_field_value_guard,
    load_front_index,
    load_group_index,
    load_group_member_guard,
    load_journal_index,
    load_member_tag_guard,
    load_message_count_index,
    load_message_index,
    load_poll_index,
    load_reminder_index,
    load_revision_index,
    load_tag_index,
    load_watch_token_index,
    normalize_front_interval,
)
from sheaf.services.import_dedup import (
    ImportConflictStrategy,
    candidate_key,
    count_new_members,
    load_member_match_index,
    resolve_member,
)
from sheaf.services.import_image_strip import (
    rewrite_internal_avatar_url,
    rewrite_internal_image_keys,
    rewrite_internal_image_refs_md,
    rewrite_internal_image_refs_md_to_none,
)
from sheaf.services.member_limits import enforce_import_member_cap

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


_VALID_DATE_FORMAT = {e.value for e in DateFormat}
_VALID_DESTINATION_TYPE = {e.value for e in DestinationType}
_VALID_COFRONT_REDACTION = {e.value for e in CofrontRedaction}
_VALID_PAYLOAD_SENSITIVITY = {e.value for e in PayloadSensitivity}
_VALID_INCLUDE_PRIVATE = {e.value for e in IncludePrivate}
# Group and member rules share the same include/exclude vocabulary.
_VALID_RULE_ACTION = {GroupRuleAction.INCLUDE.value, GroupRuleAction.EXCLUDE.value}

_SAFETY_APPLIES_KEYS = (
    "applies_to_members",
    "applies_to_groups",
    "applies_to_tags",
    "applies_to_fields",
    "applies_to_fronts",
    "applies_to_journals",
    "applies_to_images",
    "applies_to_revisions",
    "applies_to_notifications",
    "applies_to_reminders",
    "applies_to_polls",
    "applies_to_messages",
)


def _date_format(val: object) -> DateFormat | None:
    if isinstance(val, str) and val in _VALID_DATE_FORMAT:
        return DateFormat(val)
    return None


def _coerce_int(val: object, *, default: int, minimum: int | None = None) -> int:
    """Import-data int coercion: reject bools/junk, clamp below a floor."""
    if isinstance(val, bool) or not isinstance(val, int):
        return default
    if minimum is not None and val < minimum:
        return default
    return val


class SheafPreviewSummary:
    def __init__(self):
        self.system_name: str | None = None
        self.member_count: int = 0
        self.members: list[dict] = []
        self.front_count: int = 0
        self.group_count: int = 0
        self.tag_count: int = 0
        self.custom_field_count: int = 0
        self.journal_count: int = 0
        self.message_count: int = 0
        self.poll_count: int = 0
        self.reminder_count: int = 0
        self.channel_count: int = 0


class SheafImportResult:
    def __init__(self):
        self.members_imported: int = 0
        self.members_skipped: int = 0
        self.members_updated: int = 0
        self.fronts_imported: int = 0
        self.fronts_skipped: int = 0
        self.groups_imported: int = 0
        self.groups_skipped: int = 0
        self.tags_imported: int = 0
        self.tags_skipped: int = 0
        self.custom_fields_imported: int = 0
        self.custom_fields_skipped: int = 0
        self.journals_imported: int = 0
        self.journals_skipped: int = 0
        self.revisions_imported: int = 0
        self.revisions_skipped: int = 0
        self.messages_imported: int = 0
        self.messages_skipped: int = 0
        self.polls_imported: int = 0
        self.polls_skipped: int = 0
        self.reminders_imported: int = 0
        self.reminders_skipped: int = 0
        self.channels_imported: int = 0
        self.channels_skipped: int = 0
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
    summary.journal_count = len(data.get("journals", []))
    summary.message_count = len(data.get("messages", []))
    summary.poll_count = len(data.get("polls", []))
    summary.reminder_count = len(data.get("reminders", []))
    summary.channel_count = sum(
        len(t.get("channels", [])) for t in data.get("watch_tokens", [])
    )

    return summary


async def count_new_members_for_import(
    data: dict,
    system: System,
    db: AsyncSession,
    *,
    member_ids: list[str] | None = None,
    conflict_strategy: ImportConflictStrategy = ImportConflictStrategy.SKIP,
) -> int:
    """How many members `run_import` would CREATE for this payload.

    Mirrors the member-section candidate-key construction inside
    `run_import` (same name truncation, same match keys) without
    building Member rows. The archive importer uses this to run the
    tier member-cap check BEFORE it uploads any image blobs, so a
    cap failure never leaves freshly-written storage objects behind
    for the runner's rollback to orphan.
    """
    export_members = data.get("members", [])
    if member_ids is not None:
        selected = set(member_ids)
        export_members = [m for m in export_members if m.get("id") in selected]
    keys = [
        (
            blind_index((m_data.get("name") or "unnamed")[:100]),
            _trunc(m_data.get("pluralkit_id"), 8),
            bool(m_data.get("is_custom_front", False)),
        )
        for m_data in export_members
    ]
    index = await load_member_match_index(db, system.id)
    return count_new_members(keys, index=index, strategy=conflict_strategy)


async def run_import(
    data: dict,
    system: System,
    db: AsyncSession,
    *,
    conflict_strategy: ImportConflictStrategy = ImportConflictStrategy.SKIP,
    system_profile: bool = True,
    member_ids: list[str] | None = None,
    fronts: bool = True,
    groups: bool = True,
    tags: bool = True,
    custom_fields: bool = True,
    journals: bool = True,
    messages: bool = True,
    polls: bool = True,
    reminders: bool = True,
    notifications: bool = True,
    image_key_map: dict[str, str] | None = None,
    used_image_keys: set[str] | None = None,
) -> SheafImportResult:
    """Import Sheaf export data into the user's system.

    `image_key_map` is the archive importer's old-key -> new-key map of
    re-uploaded image blobs: internal image references (avatars, markdown
    embeds, image_keys caches) with a mapping are rewritten to the new
    key instead of being stripped, and the old keys actually referenced
    by written rows are recorded into `used_image_keys` so the caller
    can discard uploads nothing ended up using. With no map (the plain
    JSON import) every internal reference is stripped as before.
    """
    result = SheafImportResult()
    warnings: list[str] = []

    # Image-reference handling, bound once so the call sites below stay
    # one-liners. Empty map == strip mode.
    _ikm: dict[str, str] = image_key_map or {}
    _used: set[str] = used_image_keys if used_image_keys is not None else set()

    def _resolve_avatar_url(value: str | None) -> str | None:
        return rewrite_internal_avatar_url(value, _ikm, _used)

    def _resolve_md(text: str | None) -> str | None:
        return rewrite_internal_image_refs_md(text, _ikm, _used)

    def _resolve_md_to_none(text: str | None) -> str | None:
        return rewrite_internal_image_refs_md_to_none(text, _ikm, _used)

    def _resolve_image_keys(keys: list[str] | None) -> list[str]:
        return rewrite_internal_image_keys(keys, _ikm, _used)

    # Content dedup (see import_content_dedup): under skip AND update,
    # non-member rows that exactly match an existing row are skipped -
    # an in-place "update" of an exact-match content row is meaningless
    # since the match key IS the row's identity. CREATE keeps the old
    # append-everything behaviour. Each section loads its index lazily
    # below so disabled sections cost nothing.
    dedupe = conflict_strategy != ImportConflictStrategy.CREATE
    # Old-export-id -> new-object maps, hoisted to function scope so later
    # sections can resolve cross-references regardless of which earlier
    # sections the user opted into.
    old_gid_to_group: dict[str, Group] = {}
    old_journal_to_entry: dict[str, JournalEntry] = {}
    old_channel_to_new: dict[str, NotificationChannel] = {}

    # --- System profile ---
    if system_profile:
        sys_data = data.get("system")
        if sys_data:
            if sys_data.get("name"):
                system.name = sys_data["name"][:100]
            if sys_data.get("description") is not None:
                # Strip any /v1/files/... image embeds — those keys belong
                # to the exporting account, not this one.
                system.description = _resolve_md(
                    sys_data["description"]
                )
            if sys_data.get("tag") is not None:
                system.tag = sys_data["tag"][:8] if sys_data["tag"] else None
            if sys_data.get("color") is not None:
                system.color = sys_data["color"][:7] if sys_data["color"] else None
            if sys_data.get("privacy"):
                system.privacy = _privacy(sys_data["privacy"])
            # Notes are encrypted at rest. Empty-string clears (matches the
            # PATCH /systems/me semantics).
            if "note" in sys_data:
                note_val = _resolve_md_to_none(
                    sys_data.get("note")
                )
                system.note = encrypt(note_val) if note_val else None
            if sys_data.get("avatar_url") is not None:
                # An avatar key like avatars/<old_user_id>/<uuid>.png points
                # at the original account's storage; we can't fetch the
                # bytes, so drop the reference. External URLs (gravatar
                # etc.) pass through unchanged.
                system.avatar_url = _trunc(
                    _resolve_avatar_url(sys_data["avatar_url"]), 500
                )
            if "replace_fronts_default" in sys_data:
                system.replace_fronts_default = bool(
                    sys_data["replace_fronts_default"]
                )
            if "coalesce_contiguous_fronts" in sys_data:
                system.coalesce_contiguous_fronts = bool(
                    sys_data["coalesce_contiguous_fronts"]
                )
            df = _date_format(sys_data.get("date_format"))
            if df is not None:
                system.date_format = df

            # System Safety toggles + grace period + auto-pin. delete_confirmation
            # is deliberately NOT restored: importing a TOTP-requiring tier onto
            # an account without TOTP enrolled would lock destructive actions.
            safety = sys_data.get("safety") or {}
            if isinstance(safety, dict):
                if "grace_period_days" in safety:
                    system.safety_grace_period_days = _coerce_int(
                        safety["grace_period_days"], default=0, minimum=0
                    )
                for key in _SAFETY_APPLIES_KEYS:
                    if key in safety:
                        setattr(
                            system,
                            f"safety_{key}",
                            bool(safety[key]),
                        )
                if "auto_pin_first_revision" in safety:
                    system.auto_pin_first_revision = bool(
                        safety["auto_pin_first_revision"]
                    )

            # Revision-retention caps.
            retention = sys_data.get("retention") or {}
            if isinstance(retention, dict):
                for key in (
                    "journal_max_revisions",
                    "journal_max_revision_days",
                    "pinned_revision_max_per_target",
                ):
                    if key in retention and retention[key] is not None:
                        setattr(
                            system,
                            key,
                            _coerce_int(retention[key], default=0, minimum=0),
                        )

    # --- Members ---
    export_members = data.get("members", [])
    if member_ids is not None:
        selected = set(member_ids)
        export_members = [m for m in export_members if m.get("id") in selected]

    # Build candidates first (no DB writes), so the member-cap check
    # below counts only the rows this run would actually CREATE. Under
    # skip/update a pure re-import (e.g. restoring a backup over the same
    # roster) adds nothing and must not trip the cap.
    candidates: list[tuple[Member, str, set[str]]] = []
    for m_data in export_members:
        old_id = m_data.get("id", "")
        plaintext_name = (m_data.get("name") or "unnamed")[:100]
        # Rewrite (or drop) /v1/files/... image refs in the bio + note +
        # avatar. Usage is tracked per-candidate rather than into the
        # shared set: a candidate the dedup pass skips below never
        # persists, so its keys must not count as used or the archive
        # importer would keep blobs nothing references.
        member_used: set[str] = set()
        plaintext_description = rewrite_internal_image_refs_md(
            m_data.get("description"), _ikm, member_used
        )
        plaintext_note = rewrite_internal_image_refs_md_to_none(
            m_data.get("note"), _ikm, member_used
        )
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
            avatar_url=_trunc(
                rewrite_internal_avatar_url(
                    m_data.get("avatar_url"), _ikm, member_used
                ),
                500,
            ),
            banner_url=_trunc(
                rewrite_internal_avatar_url(
                    m_data.get("banner_url"), _ikm, member_used
                ),
                500,
            ),
            color=_trunc(m_data.get("color"), 7),
            birthday=_trunc(m_data.get("birthday"), 10),
            pluralkit_id=_trunc(m_data.get("pluralkit_id"), 8),
            emoji=_trunc(m_data.get("emoji"), 8),
            is_custom_front=bool(m_data.get("is_custom_front", False)),
            privacy=_privacy(m_data.get("privacy")),
            quick_switch_pin=_coerce_pin(m_data.get("quick_switch_pin")),
            notify_on_front_global=bool(
                m_data.get("notify_on_front_global", False)
            ),
            notify_on_front_self=bool(m_data.get("notify_on_front_self", False)),
            # notify_on_front_member_ids points at other members by id; remapped
            # in the second pass below once every member row exists.
        )
        candidates.append((member, old_id, member_used))

    index = await load_member_match_index(db, system.id)
    new_count = count_new_members(
        [candidate_key(m) for m, _, _ in candidates],
        index=index,
        strategy=conflict_strategy,
    )
    await enforce_import_member_cap(db, system, new_count)

    # Map old export ID → resolved Member (created / skipped / updated).
    # `written_old_ids` tracks the rows this run actually wrote, so the
    # second-pass notify remap below leaves skipped members untouched.
    old_id_to_member: dict[str, Member] = {}
    written_old_ids: set[str] = set()
    for member, old_id, member_used in candidates:
        resolution = resolve_member(
            member, index=index, strategy=conflict_strategy
        )
        if resolution.disposition == "created":
            db.add(resolution.member)
            result.members_imported += 1
            written_old_ids.add(old_id)
            _used |= member_used
        elif resolution.disposition == "updated":
            # _apply_update copies the candidate's rewritten description /
            # note / avatar onto the existing row, so its refs ARE used.
            result.members_updated += 1
            written_old_ids.add(old_id)
            _used |= member_used
        else:
            result.members_skipped += 1
        old_id_to_member[old_id] = resolution.member

    await db.flush()

    # Second pass: notify_on_front_member_ids references other members by their
    # export id, which only fully resolve once every member exists. Remap old
    # ids to the new member ids, dropping any member that didn't import. Members
    # with no such preference settle to the model default ([]). Skipped members
    # are left untouched (they keep whatever the existing row already had).
    for m_data in export_members:
        old_id = m_data.get("id", "")
        if old_id not in written_old_ids:
            continue
        member = old_id_to_member.get(old_id)
        if member is None:
            continue
        member.notify_on_front_member_ids = [
            str(o.id)
            for o in _resolve_ids(
                m_data.get("notify_on_front_member_ids"), old_id_to_member
            )
        ]

    # --- Tags ---
    old_tag_to_tag: dict[str, Tag] = {}
    if tags:
        tag_index = (
            await load_tag_index(db, system.id) if dedupe else ContentMatchIndex()
        )
        for t_data in data.get("tags", []):
            old_tid = t_data.get("id", "")
            name = (t_data.get("name") or "unnamed")[:50]
            existing_tag = tag_index.get(name) if dedupe else None
            if existing_tag is not None:
                # Same-named tag already in the system: reuse it so the
                # member associations below land on the existing row.
                old_tag_to_tag[old_tid] = existing_tag
                result.tags_skipped += 1
                continue
            tag = Tag(
                id=uuid.uuid4(),
                system_id=system.id,
                name=name,
                color=_trunc(t_data.get("color"), 7),
            )
            db.add(tag)
            tag_index.register(name, tag)
            old_tag_to_tag[old_tid] = tag
            result.tags_imported += 1

        await db.flush()

        # Wire tag-member associations. The pair guard covers both a
        # reused tag + skipped member (pair already in the DB) and a
        # duplicate pair within the file itself - either would violate
        # the association's composite primary key.
        member_tag_guard = (
            await load_member_tag_guard(db, system.id) if dedupe else PairGuard()
        )
        for t_data in data.get("tags", []):
            old_tid = t_data.get("id", "")
            tag = old_tag_to_tag.get(old_tid)
            if not tag:
                continue
            for old_mid in t_data.get("member_ids", []):
                member = old_id_to_member.get(old_mid)
                if member and member_tag_guard.add((tag.id, member.id)):
                    await db.execute(
                        member_tags.insert().values(tag_id=tag.id, member_id=member.id)
                    )

    # --- Custom fields ---
    old_field_to_def: dict[str, CustomFieldDefinition] = {}
    if custom_fields:
        # Field *definitions* dedupe by (name, type) unconditionally
        # (long-standing behaviour, independent of conflict_strategy):
        # duplicating them just litters the field list with a second
        # "Pronouns" etc. The system's own config (privacy/order/options)
        # on a reused field is left untouched.
        field_index = await load_field_def_index(db, system.id)
        for fd_data in data.get("custom_fields", []):
            old_fid = fd_data.get("id", "")
            name = (fd_data.get("name") or "field")[:100]
            ftype = _field_type(fd_data.get("field_type"))
            key = (name, ftype.value)
            field_def = field_index.get(key)
            if field_def is None:
                field_def = CustomFieldDefinition(
                    id=uuid.uuid4(),
                    system_id=system.id,
                    name=name,
                    field_type=ftype,
                    options=fd_data.get("options"),
                    order=fd_data.get("order", 0),
                    privacy=_privacy(fd_data.get("privacy")),
                )
                db.add(field_def)
                field_index.register(key, field_def)
                result.custom_fields_imported += 1
            else:
                result.custom_fields_skipped += 1
            old_field_to_def[old_fid] = field_def

        await db.flush()

        # Field values: the (field_id, member_id) pair guard is also
        # unconditional - a reused definition plus a deduped member would
        # otherwise trip the UNIQUE constraint, a hard error rather than
        # a preference. Pre-seeded with the system's existing pairs.
        value_guard = await load_field_value_guard(db, system.id)
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
                if not value_guard.add((field_def.id, member.id)):
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
        group_index = (
            await load_group_index(db, system.id) if dedupe else ContentMatchIndex()
        )
        created_group_ids: set[uuid.UUID] = set()

        # First pass: create groups without parent links
        for g_data in export_groups:
            old_gid = g_data.get("id", "")
            name = (g_data.get("name") or "unnamed")[:100]
            existing_group = group_index.get(name) if dedupe else None
            if existing_group is not None:
                # Reuse the existing same-named group so membership and
                # channel group-rules land on it.
                old_gid_to_group[old_gid] = existing_group
                result.groups_skipped += 1
                continue
            group = Group(
                id=uuid.uuid4(),
                system_id=system.id,
                name=name,
                description=_resolve_md(g_data.get("description")),
                color=_trunc(g_data.get("color"), 7),
            )
            db.add(group)
            group_index.register(name, group)
            created_group_ids.add(group.id)
            old_gid_to_group[old_gid] = group
            result.groups_imported += 1

        await db.flush()

        # Second pass: parent links and member associations. Parent
        # links are only written onto groups created THIS run - a
        # skipped group keeps whatever hierarchy it already had (skip
        # must not mutate existing rows). The pair guard covers reused
        # group + skipped member and in-file duplicates.
        group_member_guard = (
            await load_group_member_guard(db, system.id) if dedupe else PairGuard()
        )
        for g_data in export_groups:
            old_gid = g_data.get("id", "")
            group = old_gid_to_group.get(old_gid)
            if not group:
                continue

            old_parent = g_data.get("parent_id")
            if (
                group.id in created_group_ids
                and old_parent
                and old_parent in old_gid_to_group
            ):
                group.parent_id = old_gid_to_group[old_parent].id

            for old_mid in g_data.get("member_ids", []):
                member = old_id_to_member.get(old_mid)
                if member and group_member_guard.add((group.id, member.id)):
                    await db.execute(
                        group_members.insert().values(
                            group_id=group.id, member_id=member.id
                        )
                    )

    # --- Fronts ---
    if fronts:
        front_index = (
            await load_front_index(db, system.id) if dedupe else ContentMatchIndex()
        )
        fronts_swapped = 0
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

            started_at, ended_at, swapped = normalize_front_interval(
                started_at, ended_at
            )
            if swapped:
                fronts_swapped += 1

            # Dedup key: same interval, same (resolved) member set.
            # Works because skipped members resolve onto their existing
            # rows, so the ids here line up with existing front_members.
            fkey = front_key(started_at, ended_at, set(front_member_ids))
            if dedupe:
                if front_index.get(fkey) is not None:
                    result.fronts_skipped += 1
                    continue
                front_index.register(fkey)

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
        if fronts_swapped:
            warnings.append(
                f"Adjusted {fronts_swapped} front "
                f"{'entry' if fronts_swapped == 1 else 'entries'} whose end "
                "time was before the start time (swapped the two)."
            )

    # --- Journals ---
    if journals:
        journal_index = (
            await load_journal_index(db, system.id)
            if dedupe
            else ContentMatchIndex()
        )
        for j_data in data.get("journals", []):
            old_jid = j_data.get("id", "")
            old_member_id = j_data.get("member_id")
            member = None
            if old_member_id:
                # System-wide entries have member_id=None; a per-member entry
                # whose member wasn't imported has nowhere to live, so drop it.
                member = old_id_to_member.get(old_member_id)
                if member is None:
                    continue
            created = _parse_iso(j_data.get("created_at"))
            # Dedup key: (member, created_at). Bodies are encrypted
            # non-deterministically so content can't be the key; the
            # preserved source timestamp identifies the entry. Rows
            # without a created_at (malformed/legacy) always create.
            jkey = (member.id if member else None, created)
            if dedupe and created is not None:
                existing_entry = journal_index.get(jkey)
                if existing_entry is not None:
                    # Point the revision map at the existing row so its
                    # edit history attaches (and dedups) correctly.
                    old_journal_to_entry[old_jid] = existing_entry
                    result.journals_skipped += 1
                    continue
            title = j_data.get("title")
            entry = JournalEntry(
                id=uuid.uuid4(),
                system_id=system.id,
                member_id=member.id if member else None,
                title=encrypt(title) if title else None,
                body=encrypt(
                    _resolve_md(j_data.get("body")) or ""
                ),
                visibility=j_data.get("visibility") or "system",
                # The original authoring account is meaningless on this
                # instance; attribute the fallback author to the importing user.
                author_user_id=system.user_id,
                author_member_ids=[
                    str(o.id)
                    for o in _resolve_ids(
                        j_data.get("author_member_ids"), old_id_to_member
                    )
                ],
                author_member_names=_str_list(j_data.get("author_member_names")),
                # image_keys is a pre-extracted hosted-keys list; every entry
                # here is by construction a key belonging to the exporting
                # account. Drop them all rather than carrying dangling refs.
                image_keys=_resolve_image_keys(
                    _str_list(j_data.get("image_keys"))
                ),
            )
            if created:
                entry.created_at = created
            updated = _parse_iso(j_data.get("updated_at"))
            if updated:
                entry.updated_at = updated
            db.add(entry)
            if created is not None:
                journal_index.register(jkey, entry)
            old_journal_to_entry[old_jid] = entry
            result.journals_imported += 1

        await db.flush()

    # --- Content revisions (member-bio + journal-entry edit history) ---
    # Always attempted: each revision resolves its polymorphic target through
    # the member / journal maps, so bio revisions follow their member and
    # journal revisions only land if the journals section ran. Message-target
    # revisions aren't exported, so they never appear here.
    revision_index = (
        await load_revision_index(db, system.user_id)
        if dedupe
        else ContentMatchIndex()
    )
    for r_data in data.get("revisions", []):
        target_type = r_data.get("target_type")
        old_target = r_data.get("target_id")
        if target_type == ContentRevisionTarget.MEMBER_BIO.value:
            target = old_id_to_member.get(old_target)
        elif target_type == ContentRevisionTarget.JOURNAL_ENTRY.value:
            target = old_journal_to_entry.get(old_target)
        else:
            target = None
        if target is None:
            continue
        # Dedup key: (target type, resolved target, created_at) - lines
        # up with existing rows because skipped members / journals
        # resolve onto the existing targets above.
        created = _parse_iso(r_data.get("created_at"))
        rkey = (str(target_type), target.id, created)
        if dedupe and created is not None:
            if revision_index.get(rkey) is not None:
                result.revisions_skipped += 1
                continue
            revision_index.register(rkey)
        title = r_data.get("title")
        revision = ContentRevision(
            id=uuid.uuid4(),
            target_type=target_type,
            target_id=target.id,
            user_id=system.user_id,
            editor_member_ids=[
                str(o.id)
                for o in _resolve_ids(
                    r_data.get("editor_member_ids"), old_id_to_member
                )
            ],
            editor_member_names=_str_list(r_data.get("editor_member_names")),
            title=encrypt(title) if title else None,
            body=encrypt(
                _resolve_md(r_data.get("body")) or ""
            ),
            image_keys=_resolve_image_keys(
                _str_list(r_data.get("image_keys"))
            ),
            pinned_at=_parse_iso(r_data.get("pinned_at")),
        )
        if created:
            revision.created_at = created
        db.add(revision)
        result.revisions_imported += 1

    # --- Messages (board posts + replies) ---
    if messages:
        # Skip decisions use counted occurrences, not a key set:
        # Postgres freezes now() per transaction, so rows created
        # together legitimately share a created_at, and a key set would
        # wrongly drop the siblings on first import. The row index is
        # kept alongside purely for reply linkage onto skipped parents
        # (best-effort for colliding keys).
        message_counts = (
            await load_message_count_index(db, system.id)
            if dedupe
            else CountedIndex()
        )
        message_rows = (
            await load_message_index(db, system.id)
            if dedupe
            else ContentMatchIndex()
        )
        old_msg_to_new: dict[str, Message] = {}
        created_msg_ids: set[uuid.UUID] = set()
        # First pass: create posts without reply links. A member-wall post
        # whose board member wasn't imported is dropped along with the wall.
        for msg_data in data.get("messages", []):
            old_mid = msg_data.get("id", "")
            board_kind = msg_data.get("board_kind") or "system"
            board_member = None
            if board_kind == "member":
                old_board = msg_data.get("board_member_id")
                board_member = old_id_to_member.get(old_board) if old_board else None
                if board_member is None:
                    continue
            created = _parse_iso(msg_data.get("created_at"))
            mkey = (board_member.id if board_member else None, created)
            if dedupe and created is not None and message_counts.should_skip(mkey):
                existing_msg = message_rows.get(mkey)
                if existing_msg is not None:
                    old_msg_to_new[old_mid] = existing_msg
                result.messages_skipped += 1
                continue
            old_author = msg_data.get("author_member_id")
            author = old_id_to_member.get(old_author) if old_author else None
            message = Message(
                id=uuid.uuid4(),
                system_id=system.id,
                board_kind=board_kind,
                board_member_id=board_member.id if board_member else None,
                author_member_id=author.id if author else None,
                body=encrypt(
                    _resolve_md(msg_data.get("body")) or ""
                ),
            )
            if created:
                message.created_at = created
            updated = _parse_iso(msg_data.get("updated_at"))
            if updated:
                message.updated_at = updated
            db.add(message)
            old_msg_to_new[old_mid] = message
            created_msg_ids.add(message.id)
            result.messages_imported += 1

        await db.flush()

        # Second pass: wire reply pointers now every post exists. A parent that
        # didn't import (deleted, or on a dropped wall) leaves the reply
        # parentless, which the UI renders as "[deleted]". Only messages
        # created THIS run get a pointer written - a skipped (existing)
        # reply keeps whatever threading it already had.
        for msg_data in data.get("messages", []):
            old_parent = msg_data.get("parent_message_id")
            if not old_parent:
                continue
            child = old_msg_to_new.get(msg_data.get("id", ""))
            parent = old_msg_to_new.get(old_parent)
            if (
                child is not None
                and parent is not None
                and child.id in created_msg_ids
            ):
                child.parent_message_id = parent.id

    # --- Polls (config + current votes + audit log) ---
    if polls:
        poll_index = (
            await load_poll_index(db, system.id) if dedupe else ContentMatchIndex()
        )
        for p_data in data.get("polls", []):
            closes_at = _parse_iso(p_data.get("closes_at"))
            if not closes_at:
                warnings.append(
                    f"Skipped poll with invalid closes_at: {p_data.get('id', '?')}"
                )
                continue
            # Dedup key: created_at. Options / votes / audit events
            # belong to the poll row and skip wholesale with it.
            pcreated = _parse_iso(p_data.get("created_at"))
            if dedupe and pcreated is not None:
                if poll_index.get(pcreated) is not None:
                    result.polls_skipped += 1
                    continue
                poll_index.register(pcreated)
            description = p_data.get("description")
            poll = Poll(
                id=uuid.uuid4(),
                system_id=system.id,
                question=encrypt(p_data.get("question") or ""),
                description=encrypt(description) if description else None,
                kind=p_data.get("kind") or PollKind.SINGLE_CHOICE.value,
                results_visibility=(
                    p_data.get("results_visibility")
                    or PollResultsVisibility.LIVE.value
                ),
                closes_at=closes_at,
                retention_days=_coerce_int(
                    p_data.get("retention_days"), default=30, minimum=0
                ),
                include_custom_fronts=bool(
                    p_data.get("include_custom_fronts", False)
                ),
                restrict_voting_to_fronters=bool(
                    p_data.get("restrict_voting_to_fronters", False)
                ),
            )
            if pcreated:
                poll.created_at = pcreated
            db.add(poll)

            # Options first; vote/event option references resolve through this.
            old_option_to_new: dict[str, PollOption] = {}
            for o_data in p_data.get("options", []):
                option = PollOption(
                    id=uuid.uuid4(),
                    poll_id=poll.id,
                    text=encrypt(o_data.get("text") or ""),
                    position=_coerce_int(
                        o_data.get("position"), default=0, minimum=0
                    ),
                )
                db.add(option)
                old_option_to_new[o_data.get("id", "")] = option

            # Current votes (one per member; option refs must resolve).
            for v_data in p_data.get("votes", []):
                voter = old_id_to_member.get(v_data.get("voted_as_member_id"))
                if voter is None:
                    continue
                option_ids = [
                    o.id
                    for o in _resolve_ids(
                        v_data.get("option_ids"), old_option_to_new
                    )
                ]
                if not option_ids:
                    continue
                vote = PollVote(
                    id=uuid.uuid4(),
                    poll_id=poll.id,
                    voted_as_member_id=voter.id,
                    option_ids=option_ids,
                )
                vcreated = _parse_iso(v_data.get("created_at"))
                if vcreated:
                    vote.created_at = vcreated
                vupdated = _parse_iso(v_data.get("updated_at"))
                if vupdated:
                    vote.updated_at = vupdated
                db.add(vote)

            # Append-only audit log.
            for e_data in p_data.get("events", []):
                old_voter = e_data.get("voted_as_member_id")
                voter = old_id_to_member.get(old_voter) if old_voter else None
                event = PollVoteEvent(
                    id=uuid.uuid4(),
                    poll_id=poll.id,
                    voted_as_member_id=voter.id if voter else None,
                    action=e_data.get("action") or PollVoteAction.CAST.value,
                    option_ids=[
                        o.id
                        for o in _resolve_ids(
                            e_data.get("option_ids"), old_option_to_new
                        )
                    ],
                    fronting_member_ids=[
                        o.id
                        for o in _resolve_ids(
                            e_data.get("fronting_member_ids"), old_id_to_member
                        )
                    ],
                    # The acting account is meaningless on the target instance;
                    # the member attribution above is the durable part.
                    actor_user_id=None,
                )
                ecreated = _parse_iso(e_data.get("created_at"))
                if ecreated:
                    event.created_at = ecreated
                db.add(event)

            result.polls_imported += 1

        await db.flush()

    # --- Notification config (watch tokens + channels + rules) ---
    # Per-instance recipient state (activation hashes, push subscriptions,
    # webhook secrets, delivery bookkeeping) is omitted by the exporter and
    # not reconstructed here; channels land in their default
    # pending_registration state so the owner re-activates / re-enters the
    # secret on the new instance.
    if notifications:
        token_index = (
            await load_watch_token_index(db, system.id)
            if dedupe
            else ContentMatchIndex()
        )
        channel_index = (
            await load_channel_index(db, system.id)
            if dedupe
            else ContentMatchIndex()
        )
        for t_data in data.get("watch_tokens", []):
            tcreated = _parse_iso(t_data.get("created_at"))
            token = None
            if dedupe and tcreated is not None:
                # A skipped token still hosts the channel walk below -
                # new channels under a re-imported token attach to the
                # existing row.
                token = token_index.get(tcreated)
            if token is None:
                token = WatchToken(
                    id=uuid.uuid4(),
                    system_id=system.id,
                    label=_trunc(t_data.get("label"), 120),
                    revoked_at=_parse_iso(t_data.get("revoked_at")),
                )
                if tcreated:
                    token.created_at = tcreated
                db.add(token)
                if tcreated is not None:
                    token_index.register(tcreated, token)

            for c_data in t_data.get("channels", []):
                ccreated = _parse_iso(c_data.get("created_at"))
                if dedupe and ccreated is not None:
                    existing_channel = channel_index.get(ccreated)
                    if existing_channel is not None:
                        # Reuse the existing channel (reminders below
                        # resolve onto it); its rules are left untouched.
                        old_channel_to_new[c_data.get("id", "")] = existing_channel
                        result.channels_skipped += 1
                        continue
                dest_type = c_data.get("destination_type")
                if dest_type not in _VALID_DESTINATION_TYPE:
                    warnings.append(
                        f"Skipped channel with unknown destination type: "
                        f"{dest_type!r}"
                    )
                    continue
                config = c_data.get("destination_config")
                channel = NotificationChannel(
                    id=uuid.uuid4(),
                    watch_token_id=token.id,
                    name=(c_data.get("name") or "channel")[:120],
                    destination_type=dest_type,
                    destination_config=config if isinstance(config, dict) else {},
                    event_type=c_data.get("event_type") or "front_change",
                    base_all_members=bool(c_data.get("base_all_members", False)),
                    base_include_private=bool(
                        c_data.get("base_include_private", False)
                    ),
                    trigger_on_start=bool(c_data.get("trigger_on_start", True)),
                    trigger_on_stop=bool(c_data.get("trigger_on_stop", False)),
                    trigger_on_cofront_change=bool(
                        c_data.get("trigger_on_cofront_change", False)
                    ),
                    cofront_redaction=(
                        c_data["cofront_redaction"]
                        if c_data.get("cofront_redaction")
                        in _VALID_COFRONT_REDACTION
                        else CofrontRedaction.COUNT.value
                    ),
                    payload_sensitivity=(
                        c_data["payload_sensitivity"]
                        if c_data.get("payload_sensitivity")
                        in _VALID_PAYLOAD_SENSITIVITY
                        else PayloadSensitivity.FULL.value
                    ),
                    debounce_seconds=_coerce_int(
                        c_data.get("debounce_seconds"), default=30, minimum=0
                    ),
                    aggregation_window_seconds=_coerce_int(
                        c_data.get("aggregation_window_seconds"),
                        default=0,
                        minimum=0,
                    ),
                    quiet_hours=(
                        c_data["quiet_hours"]
                        if isinstance(c_data.get("quiet_hours"), dict)
                        else None
                    ),
                )
                if ccreated:
                    channel.created_at = ccreated
                db.add(channel)
                if ccreated is not None:
                    channel_index.register(ccreated, channel)
                old_channel_to_new[c_data.get("id", "")] = channel

                # Group rules resolve against imported groups.
                for gr in c_data.get("group_rules", []):
                    group = old_gid_to_group.get(gr.get("group_id"))
                    if group is None:
                        continue
                    rule = gr.get("rule")
                    if rule not in _VALID_RULE_ACTION:
                        continue
                    inc = gr.get("include_private")
                    db.add(
                        NotificationChannelGroupRule(
                            channel_id=channel.id,
                            group_id=group.id,
                            rule=rule,
                            include_private=(
                                inc
                                if inc in _VALID_INCLUDE_PRIVATE
                                else IncludePrivate.INHERIT.value
                            ),
                        )
                    )

                # Member rules resolve against imported members.
                for mr in c_data.get("member_rules", []):
                    member = old_id_to_member.get(mr.get("member_id"))
                    if member is None:
                        continue
                    rule = mr.get("rule")
                    if rule not in _VALID_RULE_ACTION:
                        continue
                    db.add(
                        NotificationChannelMemberRule(
                            channel_id=channel.id,
                            member_id=member.id,
                            rule=rule,
                        )
                    )

                result.channels_imported += 1

        await db.flush()

    # --- Reminders ---
    if reminders:
        reminder_index = (
            await load_reminder_index(db, system.id)
            if dedupe
            else ContentMatchIndex()
        )
        for rm_data in data.get("reminders", []):
            channel = old_channel_to_new.get(rm_data.get("channel_id"))
            if channel is None:
                # channel_id is a NOT NULL FK; a reminder can't outlive its
                # channel. If notifications weren't imported, every reminder
                # lands here.
                warnings.append(
                    f"Skipped reminder '{rm_data.get('name', '?')}' - its "
                    "notification channel was not imported"
                )
                continue
            rcreated = _parse_iso(rm_data.get("created_at"))
            if dedupe and rcreated is not None:
                if reminder_index.get(rcreated) is not None:
                    result.reminders_skipped += 1
                    continue
                reminder_index.register(rcreated)
            old_tm = rm_data.get("trigger_member_id")
            trigger_member = old_id_to_member.get(old_tm) if old_tm else None
            if old_tm and trigger_member is None:
                warnings.append(
                    f"Reminder '{rm_data.get('name', '?')}' trigger member "
                    "was not imported; kept without a member trigger"
                )
            body = _resolve_md_to_none(rm_data.get("body"))
            reminder = Reminder(
                id=uuid.uuid4(),
                system_id=system.id,
                channel_id=channel.id,
                name=(rm_data.get("name") or "reminder")[:120],
                title=encrypt(rm_data.get("title") or ""),
                body=encrypt(body) if body else None,
                enabled=bool(rm_data.get("enabled", True)),
                trigger_type=rm_data.get("trigger_type") or "repeated",
                trigger_member_id=trigger_member.id if trigger_member else None,
                trigger_event=_trunc(rm_data.get("trigger_event"), 16),
                delay_seconds=(
                    _coerce_int(rm_data.get("delay_seconds"), default=0, minimum=0)
                    if rm_data.get("delay_seconds") is not None
                    else None
                ),
                schedule_kind=_trunc(rm_data.get("schedule_kind"), 16),
                schedule_time=_trunc(rm_data.get("schedule_time"), 5),
                schedule_dow_mask=(
                    _coerce_int(
                        rm_data.get("schedule_dow_mask"), default=0, minimum=0
                    )
                    if rm_data.get("schedule_dow_mask") is not None
                    else None
                ),
                schedule_dom=(
                    _coerce_int(rm_data.get("schedule_dom"), default=0, minimum=0)
                    if rm_data.get("schedule_dom") is not None
                    else None
                ),
                schedule_tz=_trunc(rm_data.get("schedule_tz"), 64),
                cron_expression=_trunc(rm_data.get("cron_expression"), 120),
                scope=rm_data.get("scope") or "system",
                digest_when_absent=bool(rm_data.get("digest_when_absent", True)),
            )
            if rcreated:
                reminder.created_at = rcreated
            db.add(reminder)
            await db.flush()

            for old_smid in rm_data.get("scope_member_ids", []):
                scope_member = old_id_to_member.get(old_smid)
                if scope_member:
                    await db.execute(
                        reminder_scope_members.insert().values(
                            reminder_id=reminder.id, member_id=scope_member.id
                        )
                    )
            result.reminders_imported += 1

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


def _str_list(val: object) -> list:
    """Pass through a JSONB list verbatim, or [] for missing/malformed data."""
    return val if isinstance(val, list) else []


def _resolve_ids(old_ids: object, mapping: dict) -> list:
    """Map a list of old export IDs to the new ORM objects that imported,
    dropping any that didn't (filtered out, or never present). Order is
    preserved. Callers take .id off each object as str (JSONB) or UUID (ARRAY)."""
    if not isinstance(old_ids, list):
        return []
    out = []
    for oid in old_ids:
        obj = mapping.get(oid)
        if obj is not None:
            out.append(obj)
    return out

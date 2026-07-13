"""Exact-match dedup for non-member import content.

`import_dedup` makes re-imported MEMBERS resolve onto the existing
roster; this module does the same for everything members own, so a full
re-import (especially the Sheaf native restore path) is idempotent
instead of appending a second copy of every group, front, journal,
message, poll, reminder, and notification channel.

Matching is exact and system-scoped. Titles and bodies are encrypted
at rest (non-deterministically), so content rows match on plaintext
columns that round-trip the export unchanged:

- tags / groups / custom-field definitions: name (+ field type)
- fronts: (started_at, ended_at, the resolved member set)
- journals: (member_id, created_at)
- content revisions: (target_type, resolved target id, created_at)
- messages: (board_member_id, created_at)
- polls / reminders / watch tokens / channels: created_at

created_at works as a key because every importer preserves the source
timestamp on the row it creates, so the same export always lands on
the same instants. Member-linked keys work because the member-dedup
pass resolves skipped members onto their existing rows first - the
front/journal keys then line up with what is already in the DB.

Semantics: rows dedup under both SKIP and UPDATE strategies - an
in-place "update" of an exact-match content row is meaningless by
construction (the key IS the content identity). CREATE keeps the old
append-everything behaviour. Callers gate on
`strategy != ImportConflictStrategy.CREATE`.

Association tables (group_members, member_tags, reminder scopes) get
pair-set guards: a skipped group plus a newly-created member must
still link, while a skipped group plus a skipped member must not
violate the composite primary key.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.content_revision import ContentRevision
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.journal_entry import JournalEntry
from sheaf.models.member import front_members, group_members, member_tags
from sheaf.models.message import Message
from sheaf.models.notification_channel import NotificationChannel
from sheaf.models.poll import Poll
from sheaf.models.relationship import (
    GroupRelationship,
    MemberRelationship,
    RelationshipType,
)
from sheaf.models.reminder import Reminder
from sheaf.models.tag import Tag
from sheaf.models.watch_token import WatchToken


class ContentMatchIndex:
    """One section's exact-match index.

    Maps match key -> an opaque value: the existing ORM row where the
    caller links downstream records against it (groups, journals,
    field definitions, messages), or just True where existence is all
    that matters (fronts, polls, reminders).

    `get(key)` returns the existing value (a duplicate - skip the
    candidate, link against the returned row if applicable) or None
    when the key is new. The caller then builds the row and calls
    `register(key, row)` so later rows in the same import batch dedup
    against it too.
    """

    def __init__(self, rows: dict | None = None):
        self._rows: dict = dict(rows) if rows else {}

    def get(self, key: Any) -> Any | None:
        return self._rows.get(key)

    def register(self, key: Any, value: Any = True) -> None:
        self._rows[key] = value

    def __contains__(self, key: Any) -> bool:
        return key in self._rows


class CountedIndex:
    """Occurrence-counted dedup for sections whose match key can
    legitimately collide WITHIN one import.

    Chat-style messages carry second-precision source timestamps, so
    two distinct messages in the same second share a key. A plain
    key-set would wrongly drop the second one on first import. Instead,
    `should_skip(key)` consumes one existing occurrence per call: a
    re-import skips exactly as many rows per key as the system already
    holds and creates the rest, which is idempotent across runs while
    never dropping distinct same-second rows on the first pass.
    """

    def __init__(self, counts: dict | None = None):
        self._remaining: dict = dict(counts) if counts else {}

    def should_skip(self, key: Any) -> bool:
        remaining = self._remaining.get(key, 0)
        if remaining > 0:
            self._remaining[key] = remaining - 1
            return True
        return False


class PairGuard:
    """Existence guard for association-table inserts.

    `add(pair)` returns True exactly once per pair (insert it), False
    on repeats (it already exists in the DB or earlier in this batch).
    """

    def __init__(self, existing: set | None = None):
        self._pairs: set = set(existing) if existing else set()

    def add(self, pair: tuple) -> bool:
        if pair in self._pairs:
            return False
        self._pairs.add(pair)
        return True


# --- Section loaders ---------------------------------------------------------
#
# Each returns the section's existing match keys for one system, shaped
# for ContentMatchIndex / PairGuard. All read-only.


async def load_tag_index(db: AsyncSession, system_id: uuid.UUID) -> ContentMatchIndex:
    rows = await db.execute(select(Tag).where(Tag.system_id == system_id))
    return ContentMatchIndex({t.name: t for t in rows.scalars().all()})


async def load_group_index(
    db: AsyncSession, system_id: uuid.UUID
) -> ContentMatchIndex:
    rows = await db.execute(select(Group).where(Group.system_id == system_id))
    return ContentMatchIndex({g.name: g for g in rows.scalars().all()})


async def load_field_def_index(
    db: AsyncSession, system_id: uuid.UUID
) -> ContentMatchIndex:
    """Custom-field definitions keyed by (name, type) - the same dedup
    key the native importer has always used for definitions."""
    rows = await db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.system_id == system_id
        )
    )
    return ContentMatchIndex(
        {(fd.name, str(fd.field_type)): fd for fd in rows.scalars().all()}
    )


async def load_field_value_guard(
    db: AsyncSession, system_id: uuid.UUID
) -> PairGuard:
    """(field_id, member_id) pairs already holding a value - the
    UNIQUE(field_id, member_id) constraint makes a blind re-insert a
    hard error, so every importer must consult this before adding."""
    rows = await db.execute(
        select(CustomFieldValue.field_id, CustomFieldValue.member_id).join(
            CustomFieldDefinition,
            CustomFieldValue.field_id == CustomFieldDefinition.id,
        ).where(CustomFieldDefinition.system_id == system_id)
    )
    return PairGuard({(r.field_id, r.member_id) for r in rows})


async def load_front_index(
    db: AsyncSession, system_id: uuid.UUID
) -> ContentMatchIndex:
    """Fronts keyed by (started_at, ended_at, frozenset of member ids)."""
    front_rows = await db.execute(
        select(Front.id, Front.started_at, Front.ended_at).where(
            Front.system_id == system_id
        )
    )
    fronts = {r.id: (r.started_at, r.ended_at) for r in front_rows}
    members_by_front: dict[uuid.UUID, set[uuid.UUID]] = {}
    if fronts:
        assoc = await db.execute(
            select(front_members.c.front_id, front_members.c.member_id).where(
                front_members.c.front_id.in_(fronts)
            )
        )
        for r in assoc:
            members_by_front.setdefault(r.front_id, set()).add(r.member_id)
    return ContentMatchIndex(
        {
            (started, ended, frozenset(members_by_front.get(fid, set()))): True
            for fid, (started, ended) in fronts.items()
        }
    )


def front_key(
    started_at: datetime,
    ended_at: datetime | None,
    member_ids: set[uuid.UUID] | frozenset[uuid.UUID],
) -> tuple:
    return (started_at, ended_at, frozenset(member_ids))


def normalize_front_interval(
    started_at: datetime, ended_at: datetime | None
) -> tuple[datetime, datetime | None, bool]:
    """Ensure a closed front's end isn't before its start.

    Source exports (notably SimplyPlural) occasionally carry a front whose
    end timestamp precedes its start - transposed or otherwise corrupt data.
    The `ck_fronts_ended_after_started` DB constraint rejects those, which
    would abort the whole import, so importers normalise first: swap the two
    (the most data-preserving fix - it keeps the interval's duration and span)
    and report it. Returns `(started_at, ended_at, swapped)`; open fronts
    (ended_at is None) and already-ordered intervals pass through untouched.
    """
    if ended_at is not None and ended_at < started_at:
        return ended_at, started_at, True
    return started_at, ended_at, False


async def load_journal_index(
    db: AsyncSession, system_id: uuid.UUID
) -> ContentMatchIndex:
    """Journals keyed by (member_id-or-None, created_at), mapping to the
    row so revisions on a skipped journal attach to the existing one."""
    rows = await db.execute(
        select(JournalEntry).where(JournalEntry.system_id == system_id)
    )
    return ContentMatchIndex(
        {(j.member_id, j.created_at): j for j in rows.scalars().all()}
    )


async def load_revision_index(
    db: AsyncSession, user_id: uuid.UUID
) -> ContentMatchIndex:
    """Revisions scope by user (no system_id column on the model)."""
    rows = await db.execute(
        select(
            ContentRevision.target_type,
            ContentRevision.target_id,
            ContentRevision.created_at,
        ).where(ContentRevision.user_id == user_id)
    )
    return ContentMatchIndex(
        {(str(r.target_type), r.target_id, r.created_at): True for r in rows}
    )


async def load_message_index(
    db: AsyncSession, system_id: uuid.UUID
) -> ContentMatchIndex:
    """Messages keyed by (board_member_id, created_at), mapping to the
    row so a new reply whose parent was skipped still threads onto the
    existing parent. Used by the native importer, whose timestamps are
    microsecond-precision DB round-trips."""
    rows = await db.execute(
        select(Message).where(Message.system_id == system_id)
    )
    return ContentMatchIndex(
        {(m.board_member_id, m.created_at): m for m in rows.scalars().all()}
    )


async def load_message_count_index(
    db: AsyncSession, system_id: uuid.UUID
) -> CountedIndex:
    """Occurrence counts of (board_member_id, created_at) message keys.

    For foreign importers (PluralSpace chat, Prism conversations and
    board posts) whose source timestamps are coarse enough that
    distinct messages legitimately share a key - see CountedIndex.
    """
    rows = await db.execute(
        select(Message.board_member_id, Message.created_at).where(
            Message.system_id == system_id
        )
    )
    counts: dict[tuple, int] = {}
    for r in rows:
        key = (r.board_member_id, r.created_at)
        counts[key] = counts.get(key, 0) + 1
    return CountedIndex(counts)


async def load_poll_index(
    db: AsyncSession, system_id: uuid.UUID
) -> ContentMatchIndex:
    rows = await db.execute(
        select(Poll.created_at).where(Poll.system_id == system_id)
    )
    return ContentMatchIndex({r.created_at: True for r in rows})


async def load_reminder_index(
    db: AsyncSession, system_id: uuid.UUID
) -> ContentMatchIndex:
    rows = await db.execute(
        select(Reminder.created_at).where(Reminder.system_id == system_id)
    )
    return ContentMatchIndex({r.created_at: True for r in rows})


async def load_watch_token_index(
    db: AsyncSession, system_id: uuid.UUID
) -> ContentMatchIndex:
    rows = await db.execute(
        select(WatchToken).where(WatchToken.system_id == system_id)
    )
    return ContentMatchIndex(
        {t.created_at: t for t in rows.scalars().all()}
    )


async def load_channel_index(
    db: AsyncSession, system_id: uuid.UUID
) -> ContentMatchIndex:
    """Channels keyed by created_at, scoped via their watch token."""
    rows = await db.execute(
        select(NotificationChannel)
        .join(WatchToken, NotificationChannel.watch_token_id == WatchToken.id)
        .where(WatchToken.system_id == system_id)
    )
    return ContentMatchIndex(
        {c.created_at: c for c in rows.scalars().all()}
    )


async def load_group_member_guard(
    db: AsyncSession, system_id: uuid.UUID
) -> PairGuard:
    rows = await db.execute(
        select(group_members.c.group_id, group_members.c.member_id).join(
            Group, group_members.c.group_id == Group.id
        ).where(Group.system_id == system_id)
    )
    return PairGuard({(r.group_id, r.member_id) for r in rows})


async def load_member_tag_guard(
    db: AsyncSession, system_id: uuid.UUID
) -> PairGuard:
    rows = await db.execute(
        select(member_tags.c.tag_id, member_tags.c.member_id).join(
            Tag, member_tags.c.tag_id == Tag.id
        ).where(Tag.system_id == system_id)
    )
    return PairGuard({(r.tag_id, r.member_id) for r in rows})


async def load_relationship_type_index(
    db: AsyncSession, system_id: uuid.UUID
) -> ContentMatchIndex:
    """Relationship types keyed by name (like tags/groups) - a re-import
    reuses a same-named type so its edges land on the existing row."""
    rows = await db.execute(
        select(RelationshipType).where(RelationshipType.system_id == system_id)
    )
    return ContentMatchIndex({rt.name: rt for rt in rows.scalars().all()})


def relationship_pair_key(
    type_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> tuple:
    """Guard key matching the functional unique index on member/group edges:
    (type, least(source, target), greatest(source, target)). Uniqueness is
    over the UNORDERED pair per type, so direction doesn't change the key."""
    lo, hi = (source_id, target_id) if source_id <= target_id else (target_id, source_id)
    return (type_id, lo, hi)


async def load_member_relationship_guard(
    db: AsyncSession, system_id: uuid.UUID
) -> PairGuard:
    """Existing member-edge unordered pairs per type - the functional unique
    index makes a blind re-insert (or an in-file inverse duplicate) a hard
    error, so importers consult this before adding an edge."""
    rows = await db.execute(
        select(
            MemberRelationship.relationship_type_id,
            MemberRelationship.source_id,
            MemberRelationship.target_id,
        ).where(MemberRelationship.system_id == system_id)
    )
    return PairGuard(
        {
            relationship_pair_key(r.relationship_type_id, r.source_id, r.target_id)
            for r in rows
        }
    )


async def load_group_relationship_guard(
    db: AsyncSession, system_id: uuid.UUID
) -> PairGuard:
    """Group-edge counterpart of load_member_relationship_guard."""
    rows = await db.execute(
        select(
            GroupRelationship.relationship_type_id,
            GroupRelationship.source_id,
            GroupRelationship.target_id,
        ).where(GroupRelationship.system_id == system_id)
    )
    return PairGuard(
        {
            relationship_pair_key(r.relationship_type_id, r.source_id, r.target_id)
            for r in rows
        }
    )

"""Board messages service layer.

Owns the read-model helpers (board listings, unread counts, front-start
prompt assembly) plus the cascade walk used by thread-delete. The
write-side (post / edit / delete) is implemented inline at the API
layer because it's straightforward CRUD and reuses
`capture_revision` / `delete_revisions_for` from the journals service
for revision history.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import decrypt, encrypt
from sheaf.models.content_revision import ContentRevision, ContentRevisionTarget
from sheaf.models.member import Member
from sheaf.models.message import BoardKind, Message, MessageReadState
from sheaf.models.user import User
from sheaf.schemas.message import BoardSummary

# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------


def encrypt_body(plaintext: str) -> str:
    return encrypt(plaintext)


def decrypt_body(ciphertext: str | None) -> str:
    if not ciphertext:
        return ""
    return decrypt(ciphertext)


# Body shown inline as "Replying to X: <preview>". Plaintext, truncated.
_PREVIEW_LEN = 140


def preview_for(plaintext: str) -> str:
    if len(plaintext) <= _PREVIEW_LEN:
        return plaintext
    return plaintext[: _PREVIEW_LEN - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Thread cascade
# ---------------------------------------------------------------------------


async def collect_thread_ids(
    db: AsyncSession, root_id: uuid.UUID
) -> list[uuid.UUID]:
    """Return the ids of `root_id` plus every descendant via
    `parent_message_id`. Used by the thread-delete finalize path to
    sweep a whole reply chain.

    Walked breadth-first in app code; deep threads are unusual so this
    is fine without a recursive CTE.
    """
    seen: set[uuid.UUID] = {root_id}
    frontier: list[uuid.UUID] = [root_id]
    out: list[uuid.UUID] = [root_id]
    while frontier:
        result = await db.execute(
            select(Message.id).where(
                Message.parent_message_id.in_(frontier),
                Message.deleted_at.is_(None),
            )
        )
        next_ids = [row[0] for row in result.all()]
        next_frontier: list[uuid.UUID] = []
        for mid in next_ids:
            if mid not in seen:
                seen.add(mid)
                out.append(mid)
                next_frontier.append(mid)
        frontier = next_frontier
    return out


# ---------------------------------------------------------------------------
# Read-state helpers
# ---------------------------------------------------------------------------


def _board_match_clause(board_kind: str, board_member_id: uuid.UUID | None):
    """Reusable filter for "messages on this exact board"."""
    if board_kind == BoardKind.SYSTEM.value:
        return and_(
            Message.board_kind == BoardKind.SYSTEM.value,
            Message.board_member_id.is_(None),
        )
    return and_(
        Message.board_kind == BoardKind.MEMBER.value,
        Message.board_member_id == board_member_id,
    )


async def get_or_create_read_state(
    db: AsyncSession,
    *,
    member_id: uuid.UUID,
    board_kind: str,
    board_member_id: uuid.UUID | None,
) -> MessageReadState:
    # Concurrency-safe get-or-create. The messages page fires several
    # board-touching requests in parallel (get_board, mark-seen, unread),
    # so a plain select-then-insert races: two callers both miss the select
    # and both insert. For a per-member board that trips the unique index
    # (500); for the system board, where board_member_id is NULL, the
    # NULLS-NOT-DISTINCT index now still catches it. INSERT ... ON CONFLICT
    # DO NOTHING collapses the race into a no-op, then we read the winner.
    insert_stmt = (
        pg_insert(MessageReadState)
        .values(
            id=uuid.uuid4(),
            member_id=member_id,
            board_kind=board_kind,
            board_member_id=board_member_id,
            # Default to "now" so a freshly-created member doesn't
            # see every historical message as unread.
            last_seen_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(
            index_elements=["member_id", "board_kind", "board_member_id"]
        )
    )
    await db.execute(insert_stmt)

    result = await db.execute(
        select(MessageReadState).where(
            MessageReadState.member_id == member_id,
            MessageReadState.board_kind == board_kind,
            (
                MessageReadState.board_member_id == board_member_id
                if board_member_id is not None
                else MessageReadState.board_member_id.is_(None)
            ),
        )
    )
    return result.scalar_one()


async def mark_seen(
    db: AsyncSession,
    *,
    member_id: uuid.UUID,
    board_kind: str,
    board_member_id: uuid.UUID | None,
) -> None:
    state = await get_or_create_read_state(
        db,
        member_id=member_id,
        board_kind=board_kind,
        board_member_id=board_member_id,
    )
    state.last_seen_at = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Board summaries (Members tab + sidebar)
# ---------------------------------------------------------------------------


# Per-board aggregation: one COUNT + MAX + latest-body lookup per board
# plus per-board unread count derived from the caller's read_state row
# (LEFT JOIN-ed on the matching (board_kind, board_member_id)).
# Replaces what was previously a full-table message scan into Python
# dicts. Called 3x around front-start, so cutting it cold pays back
# fast on systems with thousands of messages.
_BOARD_AGG_SQL = text(
    """
    SELECT m.board_kind,
           m.board_member_id,
           COUNT(*)::int AS total_count,
           MAX(m.created_at) AS last_at,
           (array_agg(m.body ORDER BY m.created_at DESC))[1] AS last_body,
           COUNT(*) FILTER (
             WHERE rs.last_seen_at IS NOT NULL
               AND m.created_at > rs.last_seen_at
           )::int AS unread_count,
           rs.last_seen_at IS NOT NULL AS has_read_state
    FROM messages m
    LEFT JOIN message_read_state rs
      ON rs.member_id = :caller_member_id
      AND rs.board_kind = m.board_kind
      AND rs.board_member_id IS NOT DISTINCT FROM m.board_member_id
    WHERE m.system_id = :system_id AND m.deleted_at IS NULL
    GROUP BY m.board_kind, m.board_member_id, rs.last_seen_at
    """
)


async def board_summaries(
    db: AsyncSession,
    *,
    system_id: uuid.UUID,
    caller_member_id: uuid.UUID | None,
) -> list[BoardSummary]:
    """Build the Members-tab listing.

    Includes the system board first, then every member's wall ordered
    by most-recent-message. Members with zero messages still appear so
    the user can find them and post.
    """
    # One aggregation query for every non-empty board on the system.
    # When `caller_member_id` is NULL the join no-ops and the unread
    # / has_read_state columns come back as 0 / FALSE — fine for the
    # unauthenticated / non-member callers we still want to serve.
    agg_rows = await db.execute(
        _BOARD_AGG_SQL,
        {
            "caller_member_id": caller_member_id,
            "system_id": system_id,
        },
    )
    per_board: dict[
        tuple[str, uuid.UUID | None],
        tuple[int, datetime | None, bytes | None, int, bool],
    ] = {}
    for board_kind, board_member_id, total, last_at, last_body, unread, has_rs in agg_rows:
        per_board[(board_kind, board_member_id)] = (
            total, last_at, last_body, unread, has_rs,
        )

    members_result = await db.execute(
        select(Member).where(Member.system_id == system_id)
    )
    members = list(members_result.scalars().all())

    # Lazy-create read_state rows on first view so future calls have a
    # baseline to count unread-since. The old code did this per-board
    # inside the unread loop; here we batch by computing the missing
    # set up front and creating only those rows. Empty boards still
    # get a baseline so the next post to them is counted as unread.
    if caller_member_id is not None:
        all_keys: set[tuple[str, uuid.UUID | None]] = {
            (BoardKind.SYSTEM.value, None),
            *((BoardKind.MEMBER.value, m.id) for m in members),
        }
        seen_keys = {
            key for key, (_t, _la, _lb, _u, has_rs) in per_board.items() if has_rs
        }
        # Also consider boards that exist as read_state rows but have
        # no messages: the per_board aggregation only surfaces boards
        # WITH messages, so we need a second tiny lookup for empty
        # boards the caller has already seen.
        empty_seen_rs = await db.execute(
            select(
                MessageReadState.board_kind, MessageReadState.board_member_id,
            ).where(MessageReadState.member_id == caller_member_id)
        )
        for kind, mid in empty_seen_rs.all():
            seen_keys.add((kind, mid))

        missing = all_keys - seen_keys
        for kind, mid in missing:
            await get_or_create_read_state(
                db,
                member_id=caller_member_id,
                board_kind=kind,
                board_member_id=mid,
            )

    def _summary(
        key: tuple[str, uuid.UUID | None],
        member_name: str | None,
    ) -> BoardSummary:
        row = per_board.get(key)
        if row is None:
            return BoardSummary(
                board_kind=key[0],
                board_member_id=key[1],
                member_name=member_name,
                last_message_at=None,
                last_message_preview=None,
                message_count=0,
                unread_count=0,
            )
        total, last_at, last_body, unread, has_rs = row
        return BoardSummary(
            board_kind=key[0],
            board_member_id=key[1],
            member_name=member_name,
            last_message_at=last_at,
            last_message_preview=preview_for(decrypt_body(last_body)) if last_body else None,
            message_count=total,
            # Caller has no read_state row yet → unread is 0 (baseline
            # was just established above; subsequent posts will count).
            unread_count=unread if has_rs else 0,
        )

    summaries: list[BoardSummary] = [
        _summary((BoardKind.SYSTEM.value, None), None),
    ]

    member_summaries: list[BoardSummary] = [
        _summary((BoardKind.MEMBER.value, member.id), display_name(member))
        for member in members
    ]
    member_summaries.sort(
        key=lambda s: (
            s.last_message_at is None,
            -(s.last_message_at.timestamp() if s.last_message_at else 0),
        )
    )

    return summaries + member_summaries


def display_name(member: Member) -> str:
    if member.display_name:
        return member.display_name
    return decrypt(member.name) if member.name else "(unnamed)"


# ---------------------------------------------------------------------------
# Front-start prompt assembly
# ---------------------------------------------------------------------------


async def front_start_prompt(
    db: AsyncSession, *, system_id: uuid.UUID, member_id: uuid.UUID
) -> tuple[uuid.UUID, list[BoardSummary], int]:
    """Build the on-front-start prompt for a member.

    Reads the member's `notify_on_front_*` opt-ins, computes unread
    counts for each opted-in board, and returns the list (filtered to
    boards with at least one unread message). Returns empty list when
    nothing's new or the member opted out of everything.
    """
    member = await db.get(Member, member_id)
    if member is None or member.system_id != system_id:
        return member_id, [], 0

    opted_global = bool(member.notify_on_front_global)
    opted_self = bool(member.notify_on_front_self)
    opted_member_ids = {
        uuid.UUID(s) if isinstance(s, str) else s
        for s in (member.notify_on_front_member_ids or [])
    }

    if not opted_global and not opted_self and not opted_member_ids:
        return member_id, [], 0

    summaries = await board_summaries(
        db, system_id=system_id, caller_member_id=member_id
    )

    relevant: list[BoardSummary] = []
    for s in summaries:
        if s.unread_count == 0:
            continue
        is_system_board = (
            s.board_kind == BoardKind.SYSTEM.value and opted_global
        )
        is_self_wall = (
            s.board_kind == BoardKind.MEMBER.value
            and s.board_member_id == member_id
            and opted_self
        )
        is_watched_wall = (
            s.board_kind == BoardKind.MEMBER.value
            and s.board_member_id is not None
            and s.board_member_id in opted_member_ids
        )
        if is_system_board or is_self_wall or is_watched_wall:
            relevant.append(s)

    total = sum(s.unread_count for s in relevant)
    return member_id, relevant, total


# ---------------------------------------------------------------------------
# Read helpers used by the API
# ---------------------------------------------------------------------------


async def list_messages(
    db: AsyncSession,
    *,
    system_id: uuid.UUID,
    board_kind: str,
    board_member_id: uuid.UUID | None,
    limit: int = 100,
    before: datetime | None = None,
) -> list[Message]:
    stmt = (
        select(Message)
        .where(
            Message.system_id == system_id,
            Message.deleted_at.is_(None),
            _board_match_clause(board_kind, board_member_id),
        )
        # id is the deterministic tiebreaker for rows sharing a created_at,
        # so ordering stays stable across pages.
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    if before is not None:
        stmt = stmt.where(Message.created_at < before)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def fetch_parent_preview(
    db: AsyncSession, parent_id: uuid.UUID | None
) -> tuple[str | None, str | None, uuid.UUID | None]:
    """Returns (body_preview, author_display_name, parent_id) for the
    "Replying to X: ..." backlink. Returns Nones if the parent is
    missing or deleted."""
    if parent_id is None:
        return None, None, None
    parent = await db.get(Message, parent_id)
    if parent is None or parent.deleted_at is not None:
        return None, None, parent_id
    body_pt = decrypt_body(parent.body)
    author_name: str | None = None
    if parent.author_member_id is not None:
        author = await db.get(Member, parent.author_member_id)
        if author is not None:
            author_name = display_name(author)
    return preview_for(body_pt), author_name, parent_id


async def author_display_name(
    db: AsyncSession, author_member_id: uuid.UUID | None
) -> str | None:
    if author_member_id is None:
        return None
    author = await db.get(Member, author_member_id)
    return display_name(author) if author is not None else None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


async def member_belongs_to_system(
    db: AsyncSession, *, member_id: uuid.UUID, system_id: uuid.UUID
) -> bool:
    member = await db.get(Member, member_id)
    return member is not None and member.system_id == system_id


# ---------------------------------------------------------------------------
# Revision restore
# ---------------------------------------------------------------------------


async def restore_message_revision(
    *,
    db: AsyncSession,
    user: User,
    message: Message,
    revision: ContentRevision,
) -> Message:
    """Restore a message body from a revision.

    Forward-action semantics matching `restore_journal_revision` /
    `restore_member_bio_revision`: capture the current body as a fresh
    revision, then overwrite `message.body` with the revision's content.
    """
    from sheaf.services.journals import capture_revision, revision_plaintext

    current_body = decrypt_body(message.body)
    await capture_revision(
        db=db,
        target_type=ContentRevisionTarget.MESSAGE,
        target_id=message.id,
        user=user,
        system_id=message.system_id,
        title=None,
        body=current_body,
    )
    _, revision_body = revision_plaintext(revision)
    message.body = encrypt_body(revision_body or "")
    return message


# Imported for `or_` use elsewhere — kept here to avoid widening the
# top-level imports.
__all__ = [
    "board_summaries",
    "collect_thread_ids",
    "decrypt_body",
    "encrypt_body",
    "fetch_parent_preview",
    "front_start_prompt",
    "get_or_create_read_state",
    "list_messages",
    "mark_seen",
    "member_belongs_to_system",
    "preview_for",
    "author_display_name",
    "restore_message_revision",
    "or_",
]

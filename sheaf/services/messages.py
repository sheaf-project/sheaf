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

from sqlalchemy import and_, or_, select
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
    # Pull all live messages for the system grouped by board.
    rows_result = await db.execute(
        select(Message)
        .where(Message.system_id == system_id, Message.deleted_at.is_(None))
        .order_by(Message.created_at.desc())
    )
    messages = list(rows_result.scalars().all())

    # Index live messages by (board_kind, board_member_id).
    grouped: dict[tuple[str, uuid.UUID | None], list[Message]] = {}
    for m in messages:
        key = (m.board_kind, m.board_member_id)
        grouped.setdefault(key, []).append(m)

    members_result = await db.execute(
        select(Member).where(Member.system_id == system_id)
    )
    members = list(members_result.scalars().all())

    # Last-seen lookup for the caller member, batched in one query. We
    # also lazy-create read_state rows on first ask: the first time a
    # member loads a board summary, "now" becomes their baseline so
    # historical messages don't all flash as unread. Subsequent calls
    # see the persisted timestamp.
    last_seen_by_board: dict[tuple[str, uuid.UUID | None], datetime] = {}
    if caller_member_id is not None:
        rs_result = await db.execute(
            select(MessageReadState).where(
                MessageReadState.member_id == caller_member_id,
            )
        )
        for rs in rs_result.scalars().all():
            last_seen_by_board[(rs.board_kind, rs.board_member_id)] = (
                rs.last_seen_at
            )

    async def _unread(key: tuple[str, uuid.UUID | None]) -> int:
        if caller_member_id is None:
            return 0
        last_seen = last_seen_by_board.get(key)
        if last_seen is None:
            # First time this member sees this board — establish the
            # baseline at "now" and report zero unread. Subsequent calls
            # find the row.
            state = await get_or_create_read_state(
                db,
                member_id=caller_member_id,
                board_kind=key[0],
                board_member_id=key[1],
            )
            last_seen_by_board[key] = state.last_seen_at
            return 0
        return sum(1 for m in grouped.get(key, []) if m.created_at > last_seen)

    summaries: list[BoardSummary] = []

    # System board first.
    sys_key = (BoardKind.SYSTEM.value, None)
    sys_msgs = grouped.get(sys_key, [])
    summaries.append(
        BoardSummary(
            board_kind=BoardKind.SYSTEM.value,
            board_member_id=None,
            member_name=None,
            last_message_at=sys_msgs[0].created_at if sys_msgs else None,
            last_message_preview=(
                preview_for(decrypt_body(sys_msgs[0].body)) if sys_msgs else None
            ),
            message_count=len(sys_msgs),
            unread_count=await _unread(sys_key),
        )
    )

    # Members, sorted by their wall's most-recent-message (members with
    # no messages slot to the end).
    member_summaries: list[BoardSummary] = []
    for member in members:
        key = (BoardKind.MEMBER.value, member.id)
        msgs = grouped.get(key, [])
        member_summaries.append(
            BoardSummary(
                board_kind=BoardKind.MEMBER.value,
                board_member_id=member.id,
                member_name=display_name(member),
                last_message_at=msgs[0].created_at if msgs else None,
                last_message_preview=(
                    preview_for(decrypt_body(msgs[0].body)) if msgs else None
                ),
                message_count=len(msgs),
                unread_count=await _unread(key),
            )
        )
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

"""Messages API.

Owner-side CRUD for the system board + per-member walls, plus the
read-state and front-start-prompt helpers. All operations are gated
by the existing `members:*` and `system:*` scopes (no new scope was
introduced for messages — they share the same authorization domain
as the rest of the system's content).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.content_revision import ContentRevision, ContentRevisionTarget
from sheaf.models.member import Member
from sheaf.models.message import BoardKind, Message
from sheaf.models.pending_action import PendingActionType
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.journal import (
    ContentRevisionRead,
    PinRevisionRequest,
    RestoreRevisionRequest,
    UnpinRevisionRequest,
    UnpinRevisionResponse,
)
from sheaf.schemas.member import MemberDeleteConfirm
from sheaf.schemas.message import (
    BoardSummary,
    FrontStartPrompt,
    MarkSeenRequest,
    MessageCreate,
    MessageRead,
    MessagesPage,
    MessageUpdate,
    NotifyOnFrontSettings,
    UnreadCounts,
)
from sheaf.services.journals import (
    capture_revision,
    decrypt_revision_for_read,
    pin_revision,
    unpin_revision_immediate,
)
from sheaf.services.messages import (
    author_display_name,
    board_summaries,
    decrypt_body,
    display_name,
    encrypt_body,
    fetch_parent_preview,
    front_start_prompt,
    list_messages,
    member_belongs_to_system,
    preview_for,
    restore_message_revision,
)
from sheaf.services.messages import (
    mark_seen as svc_mark_seen,
)
from sheaf.services.system_safety import (
    is_safeguarded,
    pending_finalize_after_by_target,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/messages", tags=["messages"])


# --- Helpers ---------------------------------------------------------------


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )
    return system


async def _get_owned_message(
    message_id: uuid.UUID, system: System, db: AsyncSession
) -> Message:
    msg = await db.get(Message, message_id)
    if msg is None or msg.system_id != system.id or msg.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )
    return msg


async def _message_pending_map(
    db: AsyncSession, system_id: uuid.UUID
) -> dict[uuid.UUID, datetime]:
    """Union MESSAGE_DELETE + MESSAGE_THREAD_DELETE pending actions by
    target_id. Either type marks a message row for delete in the grace
    window, so the badge surfaces both."""
    direct = await pending_finalize_after_by_target(
        db, system_id, PendingActionType.MESSAGE_DELETE
    )
    thread = await pending_finalize_after_by_target(
        db, system_id, PendingActionType.MESSAGE_THREAD_DELETE
    )
    # Earliest finalize wins if a row appears in both (unlikely; defensive).
    out = dict(direct)
    for k, v in thread.items():
        if k not in out or v < out[k]:
            out[k] = v
    return out


async def _to_read(
    msg: Message,
    db: AsyncSession,
    *,
    pending_delete_at: datetime | None = None,
) -> MessageRead:
    parent_preview, parent_author, _ = await fetch_parent_preview(
        db, msg.parent_message_id
    )
    return MessageRead(
        id=msg.id,
        system_id=msg.system_id,
        board_kind=msg.board_kind,
        board_member_id=msg.board_member_id,
        author_member_id=msg.author_member_id,
        author_member_name=await author_display_name(db, msg.author_member_id),
        parent_message_id=msg.parent_message_id,
        parent_preview=parent_preview,
        parent_author_member_name=parent_author,
        body=decrypt_body(msg.body),
        created_at=msg.created_at,
        updated_at=msg.updated_at,
        pending_delete_at=pending_delete_at,
    )


async def _render_messages(
    msgs: list[Message],
    db: AsyncSession,
    *,
    pending_delete_at_by_id: dict[uuid.UUID, datetime] | None = None,
) -> list[MessageRead]:
    """Render a page of messages without the per-row N+1 that looping
    _to_read incurs. Parent messages and member names are each batched
    into a single query."""
    if not msgs:
        return []

    parent_ids = {m.parent_message_id for m in msgs if m.parent_message_id}
    parents: dict[uuid.UUID, Message] = {}
    if parent_ids:
        rows = await db.execute(select(Message).where(Message.id.in_(parent_ids)))
        parents = {p.id: p for p in rows.scalars()}

    # Members needed: authors of the page plus authors of any parent.
    member_ids = {m.author_member_id for m in msgs if m.author_member_id}
    for parent in parents.values():
        if parent.author_member_id:
            member_ids.add(parent.author_member_id)
    members: dict[uuid.UUID, Member] = {}
    if member_ids:
        rows = await db.execute(select(Member).where(Member.id.in_(member_ids)))
        members = {mem.id: mem for mem in rows.scalars()}

    def _name(member_id: uuid.UUID | None) -> str | None:
        member = members.get(member_id) if member_id else None
        return display_name(member) if member is not None else None

    rendered: list[MessageRead] = []
    for msg in msgs:
        parent_preview: str | None = None
        parent_author: str | None = None
        if msg.parent_message_id is not None:
            parent = parents.get(msg.parent_message_id)
            if parent is not None and parent.deleted_at is None:
                parent_preview = preview_for(decrypt_body(parent.body))
                parent_author = _name(parent.author_member_id)
        pending_at = (
            pending_delete_at_by_id.get(msg.id)
            if pending_delete_at_by_id
            else None
        )
        rendered.append(MessageRead(
            id=msg.id,
            system_id=msg.system_id,
            board_kind=msg.board_kind,
            board_member_id=msg.board_member_id,
            author_member_id=msg.author_member_id,
            author_member_name=_name(msg.author_member_id),
            parent_message_id=msg.parent_message_id,
            parent_preview=parent_preview,
            parent_author_member_name=parent_author,
            body=decrypt_body(msg.body),
            created_at=msg.created_at,
            updated_at=msg.updated_at,
            pending_delete_at=pending_at,
        ))
    return rendered


def _normalise_board(
    board_kind: str, board_member_id: uuid.UUID | None
) -> tuple[str, uuid.UUID | None]:
    if board_kind == BoardKind.SYSTEM.value:
        if board_member_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="board_member_id must be null for the system board.",
            )
        return BoardKind.SYSTEM.value, None
    if board_kind == BoardKind.MEMBER.value:
        if board_member_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="board_member_id required for member walls.",
            )
        return BoardKind.MEMBER.value, board_member_id
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="board_kind must be 'system' or 'member'.",
    )


# --- Listing ---------------------------------------------------------------


@router.get("/boards", response_model=list[BoardSummary])
async def get_boards(
    caller_member_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Members-tab listing: system board + every member's wall, ordered
    by most-recent message.

    `caller_member_id` (optional query param) is the perspective for
    unread counts. Pass the currently-fronting member's id to drive the
    in-app sidebar badge and the "new messages on Bob's wall" prompts.
    Without it, unread counts are zero (the rest of the response is
    still useful — it's the directory of boards).
    """
    system = await _get_user_system(user, db)
    if caller_member_id is not None and not await member_belongs_to_system(
        db, member_id=caller_member_id, system_id=system.id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="caller_member_id is not a member of this system.",
        )
    return await board_summaries(
        db, system_id=system.id, caller_member_id=caller_member_id
    )


@router.get("", response_model=MessagesPage)
async def get_board(
    board_kind: str,
    board_member_id: uuid.UUID | None = None,
    caller_member_id: uuid.UUID | None = None,
    limit: int = 100,
    before: datetime | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Paginated message list for a single board.

    Returns the most recent `limit` messages (default 100, capped at
    500). Use `before` for the next page — messages older than that
    timestamp.

    `caller_member_id` doesn't gate access (the system owner sees
    everything regardless), but its `last_seen_at` is included in the
    response so the client can render unread markers without a second
    request.
    """
    system = await _get_user_system(user, db)
    kind, member_id = _normalise_board(board_kind, board_member_id)
    if member_id is not None and not await member_belongs_to_system(
        db, member_id=member_id, system_id=system.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found.",
        )
    if caller_member_id is not None and not await member_belongs_to_system(
        db, member_id=caller_member_id, system_id=system.id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="caller_member_id is not a member of this system.",
        )

    capped_limit = max(1, min(limit, 500))
    msgs = await list_messages(
        db,
        system_id=system.id,
        board_kind=kind,
        board_member_id=member_id,
        limit=capped_limit,
        before=before,
    )
    pending_map = await _message_pending_map(db, system.id)
    rendered = await _render_messages(
        msgs, db, pending_delete_at_by_id=pending_map
    )

    caller_last_seen: datetime | None = None
    if caller_member_id is not None:
        from sheaf.services.messages import get_or_create_read_state

        state = await get_or_create_read_state(
            db,
            member_id=caller_member_id,
            board_kind=kind,
            board_member_id=member_id,
        )
        caller_last_seen = state.last_seen_at

    return MessagesPage(
        board_kind=kind,
        board_member_id=member_id,
        messages=rendered,
        caller_last_seen_at=caller_last_seen,
    )


@router.get("/unread", response_model=UnreadCounts)
async def get_unread(
    caller_member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sidebar badge data — total unread count plus per-board breakdown
    from the supplied caller member's perspective."""
    system = await _get_user_system(user, db)
    if not await member_belongs_to_system(
        db, member_id=caller_member_id, system_id=system.id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="caller_member_id is not a member of this system.",
        )
    summaries = await board_summaries(
        db, system_id=system.id, caller_member_id=caller_member_id
    )
    return UnreadCounts(
        member_id=caller_member_id,
        total=sum(s.unread_count for s in summaries),
        by_board=summaries,
    )


@router.get("/front-start-prompt", response_model=FrontStartPrompt)
async def get_front_start_prompt(
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns the boards a just-fronted member should be prompted
    about, based on their `notify_on_front_*` opt-ins. Empty list
    means "no prompt to show"."""
    system = await _get_user_system(user, db)
    if not await member_belongs_to_system(
        db, member_id=member_id, system_id=system.id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="member_id is not a member of this system.",
        )
    mid, summaries, total = await front_start_prompt(
        db, system_id=system.id, member_id=member_id
    )
    return FrontStartPrompt(
        member_id=mid, summaries=summaries, total_unread=total
    )


# --- Mark-seen ------------------------------------------------------------


@router.post("/mark-seen", status_code=status.HTTP_204_NO_CONTENT)
async def mark_seen(
    body: MarkSeenRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    system = await _get_user_system(user, db)
    if not await member_belongs_to_system(
        db, member_id=body.member_id, system_id=system.id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="member_id is not a member of this system.",
        )
    kind, board_member_id = _normalise_board(
        body.board_kind, body.board_member_id
    )
    if board_member_id is not None and not await member_belongs_to_system(
        db, member_id=board_member_id, system_id=system.id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="board_member_id is not a member of this system.",
        )
    await svc_mark_seen(
        db,
        member_id=body.member_id,
        board_kind=kind,
        board_member_id=board_member_id,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- CRUD -----------------------------------------------------------------


@router.post(
    "",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("messages:write"))],
)
async def post_message(
    body: MessageCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    kind, board_member_id = _normalise_board(
        body.board_kind, body.board_member_id
    )

    # Validate board target (member walls require the recipient to exist).
    if board_member_id is not None and not await member_belongs_to_system(
        db, member_id=board_member_id, system_id=system.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board member not found.",
        )

    # Validate author.
    if not await member_belongs_to_system(
        db, member_id=body.author_member_id, system_id=system.id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="author_member_id is not a member of this system.",
        )

    # Validate parent reply (must be on the same board, not deleted).
    if body.parent_message_id is not None:
        parent = await db.get(Message, body.parent_message_id)
        if (
            parent is None
            or parent.system_id != system.id
            or parent.deleted_at is not None
            or parent.board_kind != kind
            or parent.board_member_id != board_member_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="parent_message_id must reference a live message on the same board.",
            )

    msg = Message(
        id=uuid.uuid4(),
        system_id=system.id,
        board_kind=kind,
        board_member_id=board_member_id,
        author_member_id=body.author_member_id,
        parent_message_id=body.parent_message_id,
        body=encrypt_body(body.body),
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return await _to_read(msg, db)


@router.patch(
    "/{message_id}",
    response_model=MessageRead,
    dependencies=[Depends(require_scope("messages:write"))],
)
async def edit_message(
    message_id: uuid.UUID,
    body: MessageUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit a message body. Captures a content revision before
    overwriting, with the same auto-pin semantics journals/bios use.

    Per the v1 design, any logged-in session can edit any message —
    members don't have credentials, so per-author checks are a fiction.
    Edits are fully tracked in revision history regardless.
    """
    system = await _get_user_system(user, db)
    msg = await _get_owned_message(message_id, system, db)

    current_plaintext = decrypt_body(msg.body)
    if body.body == current_plaintext:
        return await _to_read(msg, db)

    await capture_revision(
        db=db,
        target_type=ContentRevisionTarget.MESSAGE,
        target_id=msg.id,
        user=user,
        system_id=system.id,
        title=None,
        body=current_plaintext,
    )
    msg.body = encrypt_body(body.body)
    await db.commit()
    await db.refresh(msg)
    return await _to_read(msg, db)


@router.delete(
    "/{message_id}",
    dependencies=[Depends(require_scope("messages:delete"))],
)
async def delete_message(
    message_id: uuid.UUID,
    body: MemberDeleteConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a single message. Replies are left in place and render
    with `[deleted]` as the parent. Use `/thread` for a cascade delete.
    """
    system = await _get_user_system(user, db)
    msg = await _get_owned_message(message_id, system, db)
    verify_destructive_auth(
        user,
        system,
        body.password if body else None,
        body.totp_code if body else None,
    )

    if is_safeguarded(system, PendingActionType.MESSAGE_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.MESSAGE_DELETE,
            target_id=msg.id,
            target_label=_summarise_message(msg),
        )
        await db.commit()
        await db.refresh(pending)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "pending_action_id": str(pending.id),
                "finalize_after": pending.finalize_after.isoformat(),
            },
        )

    msg.deleted_at = datetime.now(UTC)
    # Sweep this message's revisions immediately on hard-delete; the
    # safeguarded path lets the finalize handler do this when the grace
    # expires.
    from sheaf.services.journals import delete_revisions_for

    await delete_revisions_for("message", msg.id, db)
    await db.delete(msg)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{message_id}/thread",
    dependencies=[Depends(require_scope("messages:delete"))],
)
async def delete_thread(
    message_id: uuid.UUID,
    body: MemberDeleteConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Cascade-delete a message and every descendant in its reply
    chain. Separate operation from single-message delete so a future
    System Safety v2 can gate the two with different auth tiers."""
    system = await _get_user_system(user, db)
    msg = await _get_owned_message(message_id, system, db)
    verify_destructive_auth(
        user,
        system,
        body.password if body else None,
        body.totp_code if body else None,
    )

    if is_safeguarded(system, PendingActionType.MESSAGE_THREAD_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.MESSAGE_THREAD_DELETE,
            target_id=msg.id,
            target_label=f"thread: {_summarise_message(msg)}",
        )
        await db.commit()
        await db.refresh(pending)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "pending_action_id": str(pending.id),
                "finalize_after": pending.finalize_after.isoformat(),
            },
        )

    from sqlalchemy import delete as sql_delete

    from sheaf.services.journals import delete_revisions_for
    from sheaf.services.messages import collect_thread_ids

    thread_ids = await collect_thread_ids(db, msg.id)
    if thread_ids:
        # Drop revisions one-by-one (each call does its own scoped
        # query) then bulk-delete the messages themselves. The old
        # per-id `get + delete` was N+1 round-trips for what's now
        # a single DELETE ... WHERE id IN (...).
        for mid in thread_ids:
            await delete_revisions_for("message", mid, db)
        await db.execute(
            sql_delete(Message).where(Message.id.in_(thread_ids))
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{message_id}/revisions",
    response_model=list[ContentRevisionRead],
)
async def list_revisions(
    message_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    msg = await _get_owned_message(message_id, system, db)
    result = await db.execute(
        select(ContentRevision)
        .where(
            ContentRevision.target_type == ContentRevisionTarget.MESSAGE.value,
            ContentRevision.target_id == msg.id,
        )
        .order_by(ContentRevision.created_at.desc())
    )
    return [
        ContentRevisionRead.model_validate(decrypt_revision_for_read(r))
        for r in result.scalars().all()
    ]


@router.post(
    "/{message_id}/restore-revision",
    response_model=MessageRead,
    dependencies=[Depends(require_scope("messages:write"))],
)
async def restore_revision(
    message_id: uuid.UUID,
    body: RestoreRevisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    msg = await _get_owned_message(message_id, system, db)
    revision = await db.get(ContentRevision, body.revision_id)
    if (
        revision is None
        or revision.target_type != ContentRevisionTarget.MESSAGE.value
        or revision.target_id != msg.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Revision not found",
        )
    await restore_message_revision(db=db, user=user, message=msg, revision=revision)
    await db.commit()
    await db.refresh(msg)
    return await _to_read(msg, db)


@router.post(
    "/{message_id}/pin-revision",
    response_model=ContentRevisionRead,
    dependencies=[Depends(require_scope("messages:write"))],
)
async def pin_message_revision(
    message_id: uuid.UUID,
    body: PinRevisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    msg = await _get_owned_message(message_id, system, db)
    revision = await db.get(ContentRevision, body.revision_id)
    if (
        revision is None
        or revision.target_type != ContentRevisionTarget.MESSAGE.value
        or revision.target_id != msg.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Revision not found",
        )
    try:
        await pin_revision(db=db, user=user, system=system, revision=revision)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    await db.commit()
    await db.refresh(revision)
    return ContentRevisionRead.model_validate(decrypt_revision_for_read(revision))


@router.post(
    "/{message_id}/unpin-revision",
    response_model=UnpinRevisionResponse,
    dependencies=[Depends(require_scope("messages:write"))],
)
async def unpin_message_revision(
    message_id: uuid.UUID,
    body: UnpinRevisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    msg = await _get_owned_message(message_id, system, db)
    revision = await db.get(ContentRevision, body.revision_id)
    if (
        revision is None
        or revision.target_type != ContentRevisionTarget.MESSAGE.value
        or revision.target_id != msg.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Revision not found",
        )
    if revision.pinned_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Revision is not pinned",
        )

    if is_safeguarded(system, PendingActionType.REVISION_UNPIN):
        verify_destructive_auth(user, system, body.password, body.totp_code)
        target_label = f"Pinned message revision: {_summarise_message(msg)}"
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.REVISION_UNPIN,
            target_id=revision.id,
            target_label=target_label,
        )
        await db.commit()
        await db.refresh(pending)
        return UnpinRevisionResponse(
            pending_action_id=pending.id,
            finalize_after=pending.finalize_after,
        )

    unpin_revision_immediate(revision)
    await db.commit()
    await db.refresh(revision)
    return UnpinRevisionResponse(
        revision=ContentRevisionRead.model_validate(decrypt_revision_for_read(revision)),
    )


def _summarise_message(msg: Message) -> str:
    """Short description used as the System Safety pending-action
    target label. Decrypts a preview of the body."""
    from sheaf.services.messages import preview_for

    body_pt = decrypt_body(msg.body)
    return preview_for(body_pt) or "(empty message)"


# --- Per-member front-start opt-ins ---------------------------------------


@router.get(
    "/notify-settings/{member_id}",
    response_model=NotifyOnFrontSettings,
)
async def get_notify_settings(
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = await db.get(Member, member_id)
    if member is None or member.system_id != system.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )
    return NotifyOnFrontSettings(
        notify_on_front_global=member.notify_on_front_global,
        notify_on_front_self=member.notify_on_front_self,
        notify_on_front_member_ids=[
            uuid.UUID(s) if isinstance(s, str) else s
            for s in (member.notify_on_front_member_ids or [])
        ],
    )


@router.put(
    "/notify-settings/{member_id}",
    response_model=NotifyOnFrontSettings,
    dependencies=[Depends(require_scope("messages:write"))],
)
async def set_notify_settings(
    member_id: uuid.UUID,
    body: NotifyOnFrontSettings,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = await db.get(Member, member_id)
    if member is None or member.system_id != system.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    # Validate every targeted member id belongs to the same system.
    if body.notify_on_front_member_ids:
        result = await db.execute(
            select(Member.id).where(
                Member.id.in_(body.notify_on_front_member_ids),
                Member.system_id == system.id,
            )
        )
        valid = {row[0] for row in result.all()}
        unknown = [
            str(m) for m in body.notify_on_front_member_ids if m not in valid
        ]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "notify_on_front_member_ids contains ids not in this "
                    f"system: {unknown}"
                ),
            )

    member.notify_on_front_global = body.notify_on_front_global
    member.notify_on_front_self = body.notify_on_front_self
    member.notify_on_front_member_ids = [
        str(m) for m in body.notify_on_front_member_ids
    ]
    await db.commit()
    await db.refresh(member)
    return NotifyOnFrontSettings(
        notify_on_front_global=member.notify_on_front_global,
        notify_on_front_self=member.notify_on_front_self,
        notify_on_front_member_ids=[
            uuid.UUID(s) if isinstance(s, str) else s
            for s in (member.notify_on_front_member_ids or [])
        ],
    )

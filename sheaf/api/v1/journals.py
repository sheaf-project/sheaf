"""Journal entry API.

Per-member or system-wide journal entries with markdown bodies, image
embeds (via the existing /v1/files plumbing), and a polymorphic revision
history shared with member bios.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.api.v1.members import _get_user_system
from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.content_revision import ContentRevision, ContentRevisionTarget
from sheaf.models.journal_entry import JournalEntry
from sheaf.models.member import Member
from sheaf.models.pending_action import PendingActionType
from sheaf.models.user import User
from sheaf.schemas.journal import (
    ContentRevisionRead,
    JournalEntryCreate,
    JournalEntryDeleteConfirm,
    JournalEntryRead,
    JournalEntryReadWithCount,
    JournalEntryUpdate,
    JournalListResponse,
    RestoreRevisionRequest,
)
from sheaf.services.journals import (
    create_journal_entry,
    delete_revisions_for,
    restore_journal_revision,
    revision_count_for,
    update_journal_entry,
)
from sheaf.services.system_safety import (
    is_safeguarded,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/journals", tags=["journals"])


async def _get_own_entry(
    entry_id: uuid.UUID, system_id: uuid.UUID, db: AsyncSession
) -> JournalEntry:
    entry = await db.get(JournalEntry, entry_id)
    if entry is None or entry.system_id != system_id:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return entry


async def _verify_member_in_system(
    member_id: uuid.UUID, system_id: uuid.UUID, db: AsyncSession
) -> None:
    member = await db.get(Member, member_id)
    if member is None or member.system_id != system_id:
        raise HTTPException(status_code=404, detail="Member not found")


def _label_for(entry: JournalEntry) -> str:
    """Pending-action label — title if set, else timestamp fallback."""
    if entry.title:
        return entry.title
    return f"Untitled — {entry.created_at.date().isoformat()}"


@router.get("", response_model=JournalListResponse)
async def list_journals(
    member_id: uuid.UUID | None = Query(default=None),
    system_only: bool = Query(default=False),
    before: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cursor-paginated list of journal entries.

    Filter rules:
      - system_only=true → only entries with member_id IS NULL
      - member_id=<uuid> → only that member's entries
      - neither → all entries owned by the user's system
    """
    system = await _get_user_system(user, db)
    stmt = select(JournalEntry).where(JournalEntry.system_id == system.id)
    if system_only:
        stmt = stmt.where(JournalEntry.member_id.is_(None))
    elif member_id is not None:
        await _verify_member_in_system(member_id, system.id, db)
        stmt = stmt.where(JournalEntry.member_id == member_id)
    if before is not None:
        stmt = stmt.where(JournalEntry.created_at < before)
    # Fetch one extra to determine if a next cursor exists.
    stmt = stmt.order_by(JournalEntry.created_at.desc()).limit(limit + 1)
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    next_cursor = rows[limit].created_at if len(rows) > limit else None
    return JournalListResponse(
        items=[JournalEntryRead.model_validate(r) for r in rows[:limit]],
        next_cursor=next_cursor,
    )


@router.post(
    "",
    response_model=JournalEntryRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("members:write"))],
)
async def create_entry(
    body: JournalEntryCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    if body.member_id is not None:
        await _verify_member_in_system(body.member_id, system.id, db)
    try:
        entry = await create_journal_entry(
            db=db,
            user=user,
            system=system,
            member_id=body.member_id,
            title=body.title,
            body=body.body,
            visibility=body.visibility,
            author_member_ids=body.author_member_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(entry)
    return entry


@router.get("/{entry_id}", response_model=JournalEntryReadWithCount)
async def get_entry(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    entry = await _get_own_entry(entry_id, system.id, db)
    count = await revision_count_for(
        ContentRevisionTarget.JOURNAL_ENTRY, entry.id, db
    )
    payload = JournalEntryReadWithCount.model_validate(entry)
    payload.revision_count = count
    return payload


@router.patch(
    "/{entry_id}",
    response_model=JournalEntryRead,
    dependencies=[Depends(require_scope("members:write"))],
)
async def patch_entry(
    entry_id: uuid.UUID,
    body: JournalEntryUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    entry = await _get_own_entry(entry_id, system.id, db)
    update_data = body.model_dump(exclude_unset=True)
    try:
        await update_journal_entry(
            db=db,
            user=user,
            entry=entry,
            title=update_data.get("title"),
            body=update_data.get("body"),
            visibility=update_data.get("visibility"),
            author_member_ids=update_data.get("author_member_ids"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(entry)
    return entry


@router.delete(
    "/{entry_id}",
    dependencies=[Depends(require_scope("members:delete"))],
)
async def delete_entry(
    entry_id: uuid.UUID,
    body: JournalEntryDeleteConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    system = await _get_user_system(user, db)
    verify_destructive_auth(
        user,
        system,
        body.password if body else None,
        body.totp_code if body else None,
    )
    entry = await _get_own_entry(entry_id, system.id, db)

    if is_safeguarded(system, PendingActionType.JOURNAL_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.JOURNAL_DELETE,
            target_id=entry.id,
            target_label=_label_for(entry),
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

    await delete_revisions_for(
        ContentRevisionTarget.JOURNAL_ENTRY, entry.id, db
    )
    await db.delete(entry)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{entry_id}/revisions",
    response_model=list[ContentRevisionRead],
)
async def list_revisions(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    # Verify ownership of the entry first; revisions are bounded by retention
    # and don't need their own pagination in v1.
    await _get_own_entry(entry_id, system.id, db)
    result = await db.execute(
        select(ContentRevision)
        .where(
            ContentRevision.target_type
            == ContentRevisionTarget.JOURNAL_ENTRY.value,
            ContentRevision.target_id == entry_id,
        )
        .order_by(ContentRevision.created_at.desc())
    )
    return [ContentRevisionRead.model_validate(r) for r in result.scalars().all()]


@router.post(
    "/{entry_id}/restore-revision",
    response_model=JournalEntryRead,
    dependencies=[Depends(require_scope("members:write"))],
)
async def restore_revision(
    entry_id: uuid.UUID,
    body: RestoreRevisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    entry = await _get_own_entry(entry_id, system.id, db)
    revision = await db.get(ContentRevision, body.revision_id)
    if (
        revision is None
        or revision.target_type != ContentRevisionTarget.JOURNAL_ENTRY.value
        or revision.target_id != entry.id
    ):
        raise HTTPException(status_code=404, detail="Revision not found")
    await restore_journal_revision(
        db=db, user=user, entry=entry, revision=revision
    )
    await db.commit()
    await db.refresh(entry)
    return entry

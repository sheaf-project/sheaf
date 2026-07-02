import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.crypto import blind_index, encrypt
from sheaf.database import get_db
from sheaf.models.content_revision import ContentRevision, ContentRevisionTarget
from sheaf.models.front import Front
from sheaf.models.member import Member
from sheaf.models.pending_action import PendingActionType
from sheaf.models.system import System
from sheaf.models.tag import Tag
from sheaf.models.user import User
from sheaf.observability.metrics import tier_label, tier_limit_hits_total
from sheaf.schemas.journal import (
    ContentRevisionRead,
    PinRevisionRequest,
    RestoreRevisionRequest,
    UnpinRevisionRequest,
    UnpinRevisionResponse,
)
from sheaf.schemas.member import (
    MemberCreate,
    MemberDeleteConfirm,
    MemberRead,
    MemberTagUpdate,
    MemberUpdate,
)
from sheaf.schemas.tag import TagRead
from sheaf.services.analytics import clip_intervals, score_recent_fronters
from sheaf.services.journals import (
    capture_revision,
    decrypt_revision_for_read,
    delete_revisions_for,
    pin_revision,
    restore_member_bio_revision,
    unpin_revision_immediate,
)
from sheaf.services.member_limits import count_members, get_member_limit
from sheaf.services.members import decrypt_member_for_read, member_plaintext
from sheaf.services.pagination import decode_cursor, encode_cursor
from sheaf.services.system_safety import (
    is_safeguarded,
    pending_finalize_after_by_target,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/members", tags=["members"])


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")
    return system


async def _get_own_member(
    member_id: uuid.UUID, system: System, db: AsyncSession
) -> Member:
    result = await db.execute(
        select(Member).where(Member.id == member_id, Member.system_id == system.id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    return member


async def _load_bio_revision_existence(
    db: AsyncSession, member_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Return the subset of member_ids that have at least one bio
    ContentRevision. One round-trip regardless of list size."""
    if not member_ids:
        return set()
    result = await db.execute(
        select(ContentRevision.target_id)
        .where(
            ContentRevision.target_type
            == ContentRevisionTarget.MEMBER_BIO.value,
            ContentRevision.target_id.in_(member_ids),
        )
        .distinct()
    )
    return {row[0] for row in result.all()}


async def _member_has_bio_revisions(
    db: AsyncSession, member_id: uuid.UUID
) -> bool:
    result = await db.execute(
        select(ContentRevision.id)
        .where(
            ContentRevision.target_type
            == ContentRevisionTarget.MEMBER_BIO.value,
            ContentRevision.target_id == member_id,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


@router.get("", response_model=list[MemberRead])
async def list_members(
    include_archived: bool = Query(
        default=True,
        description=(
            "Include archived members. Defaults to true: archived members are "
            "soft-hidden in the UI (lists / switcher) but must stay fetchable so "
            "historical surfaces (fronts, journals) can still resolve their names. "
            "Pass false for an active-only roster."
        ),
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    # Member.name is encrypted ciphertext, so DB-side ORDER BY on it is
    # meaningless. Decrypt then sort by display_name fallback to name.
    query = select(Member).where(Member.system_id == system.id)
    if not include_archived:
        query = query.where(Member.archived_at.is_(None))
    result = await db.execute(query)
    members = list(result.scalars().all())
    with_revisions = await _load_bio_revision_existence(
        db, [m.id for m in members]
    )
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.MEMBER_DELETE
    )
    decoded = [
        decrypt_member_for_read(
            m,
            has_bio_revisions=m.id in with_revisions,
            pending_delete_at=pending.get(m.id),
        )
        for m in members
    ]
    decoded.sort(key=lambda m: (m.display_name or m.name).casefold())
    return decoded


@router.post(
    "",
    response_model=MemberRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("members:write"))],
)
async def create_member(
    body: MemberCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)

    limit = get_member_limit(user)
    if limit > 0:
        count = await count_members(db, system.id)
        if count >= limit:
            tier_limit_hits_total.labels(
                limit="members", tier=tier_label(user.tier),
            ).inc()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Member limit reached ({limit}). Contact support for an increase.",
            )

    data = body.model_dump()
    plaintext_name: str = data.pop("name")
    plaintext_description: str | None = data.pop("description", None)
    plaintext_note: str | None = data.pop("note", None)
    member = Member(
        system_id=system.id,
        name=encrypt(plaintext_name),
        name_hash=blind_index(plaintext_name),
        description=(
            encrypt(plaintext_description) if plaintext_description is not None else None
        ),
        note=(
            encrypt(plaintext_note)
            if plaintext_note is not None and plaintext_note != ""
            else None
        ),
        **data,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return decrypt_member_for_read(member)


# Quick-switch ranker tunables. Window is generous relative to the
# half-life (6 half-lives -> tail weight <2%), so the decay does the
# real shaping and the window just bounds the query.
_TOP_FRONTERS_HALF_LIFE_DAYS = 30.0
_TOP_FRONTERS_WINDOW = timedelta(days=180)


@router.get("/limit")
async def member_limit(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Effective member cap and current usage for the account.

    `limit` of 0 means unlimited (and `remaining` is null). Used by the
    import flows to warn before an import would blow the cap.
    """
    system = await _get_user_system(user, db)
    limit = get_member_limit(user)
    current = await count_members(db, system.id)
    return {
        "limit": limit,
        "current": current,
        "remaining": max(limit - current, 0) if limit > 0 else None,
    }


@router.get("/top-fronters", response_model=list[MemberRead])
async def top_fronters(
    limit: int = Query(default=8, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Members ranked for a quick-switch list.

    Pinned members (quick_switch_pin set) come first, in pin order;
    everyone else follows by a recency-weighted fronting score
    (exponential decay, 30-day half-life). Useful for autopopulating a
    start-front shortcut or a member picker. Returns at most `limit`.
    """
    system = await _get_user_system(user, db)
    now = datetime.now(UTC)
    since = now - _TOP_FRONTERS_WINDOW

    fronts_result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(
            Front.system_id == system.id,
            Front.started_at < now,
            (Front.ended_at.is_(None)) | (Front.ended_at > since),
        )
    )
    rows = [
        (f.started_at, f.ended_at, [m.id for m in f.members])
        for f in fronts_result.scalars().all()
    ]
    intervals = clip_intervals(rows, since=since, until=now)
    scores = score_recent_fronters(
        intervals, now=now, half_life_days=_TOP_FRONTERS_HALF_LIFE_DAYS
    )

    members_result = await db.execute(
        select(Member).where(
            Member.system_id == system.id,
            Member.archived_at.is_(None),
        )
    )
    members = list(members_result.scalars().all())

    pinned = sorted(
        (m for m in members if m.quick_switch_pin is not None),
        key=lambda m: (m.quick_switch_pin, str(m.id)),
    )
    # Highest score first; id as a stable tiebreaker for equal scores
    # (e.g. the long tail of members who haven't fronted in the window).
    unpinned = sorted(
        (m for m in members if m.quick_switch_pin is None),
        key=lambda m: (-scores.get(m.id, 0.0), str(m.id)),
    )
    ordered = (pinned + unpinned)[:limit]

    with_revisions = await _load_bio_revision_existence(
        db, [m.id for m in ordered]
    )
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.MEMBER_DELETE
    )
    return [
        decrypt_member_for_read(
            m,
            has_bio_revisions=m.id in with_revisions,
            pending_delete_at=pending.get(m.id),
        )
        for m in ordered
    ]


@router.get("/{member_id}", response_model=MemberRead)
async def get_member(
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db)
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.MEMBER_DELETE
    )
    return decrypt_member_for_read(
        member,
        has_bio_revisions=await _member_has_bio_revisions(db, member.id),
        pending_delete_at=pending.get(member.id),
    )


@router.patch(
    "/{member_id}",
    response_model=MemberRead,
    dependencies=[Depends(require_scope("members:write"))],
)
async def update_member(
    member_id: uuid.UUID,
    body: MemberUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db)
    update_data = body.model_dump(exclude_unset=True)
    _, current_description = member_plaintext(member)
    if (
        "description" in update_data
        and update_data["description"] != current_description
    ):
        await capture_revision(
            db=db,
            target_type=ContentRevisionTarget.MEMBER_BIO,
            target_id=member.id,
            user=user,
            system_id=system.id,
            title=None,
            body=current_description or "",
        )
    for key, value in update_data.items():
        if key == "name":
            member.name = encrypt(value)
            member.name_hash = blind_index(value)
        elif key == "description":
            member.description = encrypt(value) if value is not None else None
        elif key == "note":
            # Empty string clears the column. Notes are deliberately
            # overwrite-only; no revision capture here.
            if value is None or value == "":
                member.note = None
            else:
                member.note = encrypt(value)
        else:
            setattr(member, key, value)
    await db.commit()
    await db.refresh(member)
    return decrypt_member_for_read(
        member,
        has_bio_revisions=await _member_has_bio_revisions(db, member.id),
    )


@router.delete(
    "/{member_id}",
    dependencies=[Depends(require_scope("members:delete"))],
)
async def delete_member(
    member_id: uuid.UUID,
    body: MemberDeleteConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    system = await _get_user_system(user, db)
    await verify_destructive_auth(
        user,
        system,
        body.password if body else None,
        body.totp_code if body else None,
        db,
    )
    member = await _get_own_member(member_id, system, db)

    if is_safeguarded(system, PendingActionType.MEMBER_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.MEMBER_DELETE,
            target_id=member.id,
            target_label=member.display_name or member_plaintext(member)[0],
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

    await delete_revisions_for(ContentRevisionTarget.MEMBER_BIO, member.id, db)
    await db.delete(member)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{member_id}/archive",
    response_model=MemberRead,
    dependencies=[Depends(require_scope("members:write"))],
)
async def archive_member(
    member_id: uuid.UUID,
    body: MemberDeleteConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Archive a member: a reversible soft-hide, not a delete.

    Hidden from the members list, switcher, top-fronters, and pickers, but
    kept everywhere historical. Unlike delete there is no grace period; the
    only optional friction is re-auth when the `archive` System Safety
    category is on (and an auth tier is configured).
    """
    system = await _get_user_system(user, db)
    if system.safety_applies_to_archive:
        await verify_destructive_auth(
            user,
            system,
            body.password if body else None,
            body.totp_code if body else None,
            db,
        )
    member = await _get_own_member(member_id, system, db)
    if member.archived_at is None:
        member.archived_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(member)
    return decrypt_member_for_read(
        member,
        has_bio_revisions=await _member_has_bio_revisions(db, member.id),
    )


@router.post(
    "/{member_id}/unarchive",
    response_model=MemberRead,
    dependencies=[Depends(require_scope("members:write"))],
)
async def unarchive_member(
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Restore an archived member to active. Ungated - restoring visibility
    is not a destructive action."""
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db)
    if member.archived_at is not None:
        member.archived_at = None
        await db.commit()
        await db.refresh(member)
    return decrypt_member_for_read(
        member,
        has_bio_revisions=await _member_has_bio_revisions(db, member.id),
    )


@router.get("/{member_id}/tags", response_model=list[TagRead])
async def get_member_tags(
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the tags this member is currently labelled with."""
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Member)
        .options(selectinload(Member.tags))
        .where(Member.id == member_id, Member.system_id == system.id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )
    return sorted(member.tags, key=lambda t: t.name.casefold())


@router.put(
    "/{member_id}/tags",
    response_model=list[TagRead],
    dependencies=[Depends(require_scope("tags:write"))],
)
async def set_member_tags(
    member_id: uuid.UUID,
    body: MemberTagUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace this member's full tag set with the body-supplied list.

    Mirrors `PUT /v1/tags/{tag_id}/members` from the other side. Either
    endpoint can be used to manage the m2m; pick whichever matches the
    UI you're in (member-edit form vs tag-management page).
    """
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Member)
        .options(selectinload(Member.tags))
        .where(Member.id == member_id, Member.system_id == system.id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    if body.tag_ids:
        tag_result = await db.execute(
            select(Tag).where(
                Tag.id.in_(body.tag_ids),
                Tag.system_id == system.id,
            )
        )
        tags = list(tag_result.scalars().all())
        if len(tags) != len(set(body.tag_ids)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more tag IDs are invalid",
            )
    else:
        tags = []

    member.tags = tags
    await db.commit()
    return sorted(tags, key=lambda t: t.name.casefold())


@router.get(
    "/{member_id}/revisions",
    response_model=list[ContentRevisionRead],
)
async def list_bio_revisions(
    member_id: uuid.UUID,
    response: Response,
    # "Bounded by retention" only holds on the hosted tiers; self-hosted
    # defaults the revision cap to 0 (unlimited), and pinned revisions are
    # exempt from the sweep, so a bio's history can grow without limit. Page
    # it, matching the journal / message / front-audit revision lists. Default
    # covers the hosted Plus rolling cap (100) with headroom; self-hosted /
    # pinned-heavy bios follow the cursor. Constants inline (no config knob).
    limit: int = Query(default=200, ge=1, le=500),
    cursor: str | None = Query(
        default=None,
        description=(
            "Opaque pagination cursor. Pass the `X-Sheaf-Next-Cursor` value "
            "from the previous response to fetch the next (older) page."
        ),
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List a member bio's revision history, newest first.

    Keyset-paginated: the body stays a plain array, and `X-Sheaf-Has-More`
    / `X-Sheaf-Next-Cursor` headers signal and drive the next (older) page
    (same shape as `GET /v1/fronts` and the journal revision list)."""
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db)
    query = (
        select(ContentRevision)
        .where(
            ContentRevision.target_type
            == ContentRevisionTarget.MEMBER_BIO.value,
            ContentRevision.target_id == member.id,
        )
        # created_at is a per-transaction now(), so a burst of revisions can
        # tie; id is the deterministic tiebreaker the cursor comparison uses
        # too, keeping pages stable.
        .order_by(ContentRevision.created_at.desc(), ContentRevision.id.desc())
    )
    if cursor is not None:
        try:
            cursor_created, cursor_id = decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor",
            ) from exc
        query = query.where(
            tuple_(ContentRevision.created_at, ContentRevision.id)
            < tuple_(cursor_created, cursor_id)
        )

    # limit + 1 probe answers "is there more?" without a COUNT.
    result = await db.execute(query.limit(limit + 1))
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    page = rows[:limit]

    response.headers["X-Sheaf-Has-More"] = "true" if has_more else "false"
    if has_more and page:
        last = page[-1]
        response.headers["X-Sheaf-Next-Cursor"] = encode_cursor(
            last.created_at, last.id
        )

    return [
        ContentRevisionRead.model_validate(decrypt_revision_for_read(r))
        for r in page
    ]


@router.post(
    "/{member_id}/restore-revision",
    response_model=MemberRead,
    dependencies=[Depends(require_scope("members:write"))],
)
async def restore_bio_revision(
    member_id: uuid.UUID,
    body: RestoreRevisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db)
    revision = await db.get(ContentRevision, body.revision_id)
    if (
        revision is None
        or revision.target_type != ContentRevisionTarget.MEMBER_BIO.value
        or revision.target_id != member.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Revision not found",
        )
    await restore_member_bio_revision(
        db=db, user=user, member=member, revision=revision
    )
    await db.commit()
    await db.refresh(member)
    return decrypt_member_for_read(
        member,
        has_bio_revisions=await _member_has_bio_revisions(db, member.id),
    )


@router.post(
    "/{member_id}/pin-revision",
    response_model=ContentRevisionRead,
    dependencies=[Depends(require_scope("members:write"))],
)
async def pin_bio_revision(
    member_id: uuid.UUID,
    body: PinRevisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db)
    revision = await db.get(ContentRevision, body.revision_id)
    if (
        revision is None
        or revision.target_type != ContentRevisionTarget.MEMBER_BIO.value
        or revision.target_id != member.id
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
    "/{member_id}/unpin-revision",
    response_model=UnpinRevisionResponse,
    dependencies=[Depends(require_scope("members:write"))],
)
async def unpin_bio_revision(
    member_id: uuid.UUID,
    body: UnpinRevisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db)
    revision = await db.get(ContentRevision, body.revision_id)
    if (
        revision is None
        or revision.target_type != ContentRevisionTarget.MEMBER_BIO.value
        or revision.target_id != member.id
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
        await verify_destructive_auth(user, system, body.password, body.totp_code, db)
        member_name, _ = member_plaintext(member)
        target_label = f"Pinned bio revision: {member_name or 'Unnamed member'}"
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

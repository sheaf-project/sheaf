import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import bindparam, func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.crypto import decrypt, encrypt
from sheaf.database import get_db
from sheaf.middleware.rate_limit import check_front_switch_rate, write_rate_limit
from sheaf.models.front import Front
from sheaf.models.front_audit_event import FrontAuditEvent
from sheaf.models.member import Member
from sheaf.models.pending_action import PendingActionType
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.observability.metrics import fronts_created_total
from sheaf.schemas.front import (
    FrontAuditEventRead,
    FrontCreate,
    FrontRead,
    FrontSnapshot,
    FrontUpdate,
)
from sheaf.schemas.member import MemberDeleteConfirm
from sheaf.services.notifications.events import (
    emit_front_change,
    snapshot_front_state,
)
from sheaf.services.pagination import decode_cursor, encode_cursor
from sheaf.services.system_safety import (
    is_safeguarded,
    pending_finalize_after_by_target,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/fronts", tags=["fronts"])


# Namespace for the per-system front-switch advisory lock. Postgres keeps
# the two-int4 advisory-lock key space disjoint from the single-bigint
# space the leader election uses (sheaf/services/leader.py), so this only
# has to stay distinct from any *other* two-int4 advisory lock we might add
# later. Arbitrary but must not change across deploys.
_FRONT_SWITCH_LOCK_NS = 0x53480001  # "SH" + 0x0001: front-switch serialisation


def _system_front_lock_key(system_id: uuid.UUID) -> int:
    """Map a system id to a signed int4 for the front-switch advisory lock.

    Deterministic across processes (unlike the built-in ``hash``). A given
    system always maps to the same key, so its concurrent switches serialise
    exactly; a collision between two *different* systems only ever adds a
    little cross-system serialisation of the rare front-create path, never
    wrong data. Low 31 bits keep the value in non-negative int4 range.
    """
    return system_id.int & 0x7FFFFFFF


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")
    return system


def _front_to_read(
    front: Front,
    *,
    member_since: dict[str, datetime] | None = None,
    member_since_capped: list[str] | None = None,
    has_audit_history: bool = False,
    pending_delete_at: datetime | None = None,
) -> FrontRead:
    """Project a Front row to its API shape.

    `member_since` is the per-member effective "fronting since" map.
    When omitted, defaults to {member_id: front.started_at} for each
    member — the literal-entry view used by history endpoints and any
    caller that doesn't want to pay for the walk-back.

    `member_since_capped` lists member ids whose chain hit the
    walk-back depth limit; the returned timestamp is a lower bound,
    not the true chain start. Frontends should render those with a
    "> X ago" prefix to be honest about precision.

    `has_audit_history` reflects whether at least one FrontAuditEvent
    exists for this entry. Computed once per list call via a batch
    EXISTS query (see `_load_audit_existence`); single-front endpoints
    do their own one-row check.
    """
    if member_since is None:
        member_since = {str(m.id): front.started_at for m in front.members}
    return FrontRead(
        id=front.id,
        system_id=front.system_id,
        started_at=front.started_at,
        ended_at=front.ended_at,
        member_ids=[m.id for m in front.members],
        custom_status=decrypt(front.custom_status) if front.custom_status else None,
        member_since=member_since,
        member_since_capped=member_since_capped or [],
        has_audit_history=has_audit_history,
        pending_delete_at=pending_delete_at,
    )


async def _load_audit_existence(
    db: AsyncSession, front_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Return the subset of given front_ids that have at least one
    FrontAuditEvent row. One round-trip regardless of the list size."""
    if not front_ids:
        return set()
    result = await db.execute(
        select(FrontAuditEvent.front_id)
        .where(FrontAuditEvent.front_id.in_(front_ids))
        .distinct()
    )
    return {row[0] for row in result.all()}


async def _front_has_audit(db: AsyncSession, front_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(FrontAuditEvent.id)
        .where(FrontAuditEvent.front_id == front_id)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


# Walk-back depth cap: pathological cycles aside, real chains are
# typically 1-3 entries. 500 is a generous bound that prevents a
# corrupted-data edge case from running unbounded queries while still
# covering anyone who switches every few minutes for many hours.
# When the cap *is* hit, the response flags the affected member so the
# UI can render "> X ago" instead of silently under-reporting. Easy to
# raise later if the flag actually starts surfacing in real usage.
_COALESCE_MAX_DEPTH = 500


# Recursive CTE that walks every (seed_front, member) chain in
# parallel. Replaces what was previously a per-(front, member) loop of
# awaited single-row queries — fine for one open front with two
# members on a brand-new system, ruinous for /current on a busy
# system with coalesce_contiguous_fronts on. The CTE is bounded by
# `_COALESCE_MAX_DEPTH` so a corrupted-data cycle can't run forever,
# and we surface the cap-hit per member the same way the old code did.
_COALESCED_SINCE_SQL = text(
    """
    WITH RECURSIVE chain(seed_front_id, member_id, started_at, depth) AS (
        SELECT f.id, fm.member_id, f.started_at, 0
        FROM fronts f
        JOIN front_members fm ON fm.front_id = f.id
        WHERE f.id IN :seed_ids

        UNION ALL

        SELECT chain.seed_front_id, chain.member_id, prev.started_at,
               chain.depth + 1
        FROM chain
        JOIN fronts prev
            ON prev.system_id = :system_id
            AND prev.ended_at = chain.started_at
        JOIN front_members fm
            ON fm.front_id = prev.id
            AND fm.member_id = chain.member_id
        WHERE chain.depth < :max_depth
    )
    SELECT seed_front_id, member_id,
           MIN(started_at) AS earliest_started,
           MAX(depth) AS deepest
    FROM chain
    GROUP BY seed_front_id, member_id
    """
).bindparams(bindparam("seed_ids", expanding=True))


async def _build_coalesced_member_since(
    db: AsyncSession, system: System, fronts: list[Front]
) -> dict[uuid.UUID, tuple[dict[str, datetime], list[str]]]:
    """For each given front, build (since_map, capped_member_ids).

    When `system.coalesce_contiguous_fronts` is False, returns the
    literal-entry view for each member with no capped members.
    Otherwise walks back per member to find the earliest chain start.
    """
    out: dict[uuid.UUID, tuple[dict[str, datetime], list[str]]] = {}
    if not fronts:
        return out

    if not system.coalesce_contiguous_fronts:
        for front in fronts:
            per_member: dict[str, datetime] = {
                str(m.id): front.started_at for m in front.members
            }
            out[front.id] = (per_member, [])
        return out

    # Pre-populate the output so fronts whose members don't appear in
    # the CTE result (shouldn't happen, but defensive) still get an
    # empty entry rather than a KeyError downstream.
    for front in fronts:
        out[front.id] = ({}, [])

    rows = await db.execute(
        _COALESCED_SINCE_SQL,
        {
            "seed_ids": [f.id for f in fronts],
            "system_id": system.id,
            "max_depth": _COALESCE_MAX_DEPTH,
        },
    )

    for seed_id, member_id, earliest, deepest in rows:
        per_member, capped = out[seed_id]
        per_member[str(member_id)] = earliest
        # depth==max_depth means the recursive step ran the last
        # allowed iteration and may not have found the true chain
        # start. Same semantics as the prior per-walk cap flag.
        if deepest is not None and deepest >= _COALESCE_MAX_DEPTH:
            capped.append(str(member_id))

    return out


@router.get("", response_model=list[FrontRead])
async def list_fronts(
    response: Response,
    limit: int = Query(default=50, le=200),
    offset: int = Query(
        default=0,
        ge=0,
        # Legacy offset paging walks and discards `offset` rows server-side,
        # so an unbounded value is a cheap way to make the DB do arbitrary
        # work. Cap it; anyone paging deeper should switch to `cursor`, which
        # stays flat-cost at any depth.
        le=10_000,
        description=(
            "Legacy offset paging, bounded at 10000. For deeper history use "
            "the `cursor` param, which stays cheap at any depth."
        ),
    ),
    cursor: str | None = Query(
        default=None,
        description=(
            "Opaque pagination cursor. Pass the value of "
            "`X-Sheaf-Next-Cursor` from the previous response to fetch "
            "the next page. When set, `offset` is ignored. Callers "
            "should treat the value as a black box."
        ),
    ),
    include_total: bool = Query(
        default=False,
        description=(
            "Opt-in: include `X-Sheaf-Total-Count` header with the total "
            "number of entries in the system. Costs one extra COUNT query; "
            "set true only when the UI actually renders page numbers."
        ),
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List front entries newest-first.

    Pagination: cursor-based (preferred) or offset-based (legacy). When
    more entries exist beyond the current page, the response carries:

    - `X-Sheaf-Has-More: true|false`
    - `X-Sheaf-Next-Cursor: <opaque>` (only when `Has-More` is true)

    Detection uses a `limit + 1` probe rather than a separate `COUNT(*)`,
    so the response time stays flat regardless of total history length.
    """
    system = await _get_user_system(user, db)

    query = (
        select(Front)
        .options(selectinload(Front.members))
        .where(Front.system_id == system.id)
        # Stable order under tied started_at: id is the deterministic
        # tiebreaker the cursor's row comparison also uses, so pagination
        # neither skips nor duplicates rows.
        .order_by(Front.started_at.desc(), Front.id.desc())
    )

    if cursor is not None:
        try:
            cursor_started, cursor_id = decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor",
            ) from exc
        # Postgres row comparison: rows whose (started_at, id) is
        # lexicographically less than the cursor's pair. With the matching
        # ORDER BY, this is exactly "the next page of older entries".
        query = query.where(
            tuple_(Front.started_at, Front.id)
            < tuple_(cursor_started, cursor_id)
        )
    elif offset:
        # Legacy callers pinned to offset-based paging keep working.
        query = query.offset(offset)

    # Probe for one extra row so we can answer "is there more?" without a
    # COUNT query. Trim before returning.
    result = await db.execute(query.limit(limit + 1))
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    fronts = rows[:limit]

    response.headers["X-Sheaf-Has-More"] = "true" if has_more else "false"
    if has_more and fronts:
        last = fronts[-1]
        response.headers["X-Sheaf-Next-Cursor"] = encode_cursor(
            last.started_at, last.id
        )

    if include_total:
        # Opt-in: only when the UI is rendering numbered pages and actually
        # needs the count. COUNT(*) on a system_id-indexed filter is fast,
        # but it's still an extra round-trip we don't want every caller
        # paying for.
        total_result = await db.execute(
            select(func.count())
            .select_from(Front)
            .where(Front.system_id == system.id)
        )
        response.headers["X-Sheaf-Total-Count"] = str(total_result.scalar_one())

    with_audit = await _load_audit_existence(db, [f.id for f in fronts])
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.FRONT_DELETE
    )
    return [
        _front_to_read(
            f,
            has_audit_history=f.id in with_audit,
            pending_delete_at=pending.get(f.id),
        )
        for f in fronts
    ]


@router.get("/current", response_model=list[FrontRead])
async def get_current_fronts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(Front.system_id == system.id, Front.ended_at.is_(None))
        .order_by(Front.started_at.desc())
    )
    fronts = list(result.scalars().all())
    member_since_map = await _build_coalesced_member_since(db, system, fronts)
    with_audit = await _load_audit_existence(db, [f.id for f in fronts])
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.FRONT_DELETE
    )
    return [
        _front_to_read(
            f,
            member_since=member_since_map[f.id][0],
            member_since_capped=member_since_map[f.id][1],
            has_audit_history=f.id in with_audit,
            pending_delete_at=pending.get(f.id),
        )
        for f in fronts
    ]


@router.post(
    "",
    response_model=FrontRead,
    status_code=status.HTTP_201_CREATED,
    # write_rate_limit(): counts against the combined per-account write
    # budget shared with journals/messages/members/reminders.
    dependencies=[Depends(require_scope("fronts:write")), write_rate_limit()],
)
async def create_front(
    body: FrontCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)

    # Per-system front-switch guard, in addition to the per-account write
    # limit above. Keyed on the system rather than the caller, it catches a
    # stuck switch-client / looping integration on a system that may have
    # several legitimate writers. Checked before any front DB work so a
    # runaway loop is cut off early.
    if not await check_front_switch_rate(system.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Fronts are being created faster than we accept them for "
                "your system; check for a looping client or integration."
            ),
        )

    # Validate member IDs belong to this system
    result = await db.execute(
        select(Member).where(
            Member.id.in_(body.member_ids),
            Member.system_id == system.id,
        )
    )
    members = list(result.scalars().all())
    if len(members) != len(body.member_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more member IDs are invalid",
        )

    # Serialise front switches for this system. create_front is check-then-
    # act: it reads the open fronts to auto-end (replace) or to reject an
    # exact-duplicate member set, then inserts a new open front. Under
    # READ COMMITTED two concurrent switches each read the pre-insert state,
    # both pass their checks, and both insert - leaving two overlapping or
    # duplicate open fronts. A transaction-scoped advisory lock keyed on the
    # system serialises the whole read-decide-insert section and releases
    # automatically on commit or rollback, so a stuck request can't hold it.
    # Explicit int4 casts pin the two-key pg_advisory_xact_lock(int, int)
    # overload regardless of how the driver types the bound ints (a bare
    # bigint would fail to resolve the two-key form). Both values are built
    # to fit signed int4 above.
    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "CAST(:ns AS integer), CAST(:key AS integer))"
        ),
        {
            "ns": _FRONT_SWITCH_LOCK_NS,
            "key": _system_front_lock_key(system.id),
        },
    )

    before_state = await snapshot_front_state(db, system.id)

    # Compute the new front's started_at up-front so we can use it as the
    # boundary timestamp for any auto-ended fronts. Strict equality between
    # f_old.ended_at and f_new.started_at is what `coalesce_contiguous_fronts`
    # relies on to walk a member back through chained entries — if these
    # were two separate datetime.now() calls a few ms apart the chain
    # would always break.
    new_started_at = body.started_at or datetime.now(UTC)

    # Resolve replace_fronts: explicit value beats system default
    should_replace = (
        body.replace_fronts if body.replace_fronts is not None else system.replace_fronts_default
    )
    if should_replace:
        open_fronts = await db.execute(
            select(Front)
            .where(Front.system_id == system.id, Front.ended_at.is_(None))
        )
        for f in open_fronts.scalars().all():
            f.ended_at = new_started_at
    else:
        # Block exact-set duplicates: if an open front already has this exact
        # member set, two fronts with the same composition has no useful
        # semantics (notifications, current-front queries, etc. would treat
        # them as redundant). Different compositions are still allowed; the
        # owner can keep {Alice} fronting and add {Alice, Bob} alongside.
        new_set = set(body.member_ids)
        existing = await db.execute(
            select(Front)
            .options(selectinload(Front.members))
            .where(Front.system_id == system.id, Front.ended_at.is_(None))
        )
        for f in existing.scalars().all():
            if {m.id for m in f.members} == new_set:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "A front with these exact members is already active. "
                        "Either end the existing front first, or pick a "
                        "different combination."
                    ),
                )

    front = Front(
        system_id=system.id,
        started_at=new_started_at,
        custom_status=encrypt(body.custom_status) if body.custom_status else None,
        members=members,
    )
    db.add(front)
    await db.flush()

    after_state = await snapshot_front_state(db, system.id)
    await emit_front_change(
        db, system_id=system.id, before=before_state, after=after_state
    )

    await db.commit()
    fronts_created_total.inc()
    await db.refresh(front, ["members"])
    return _front_to_read(front)


def _front_snapshot_for_audit(front: Front) -> dict:
    """Serialise a Front into the JSONB shape stored in
    `front_audit_events.before/after_snapshot`. custom_status stays
    encrypted in the snapshot exactly as it is on the live row."""
    return {
        "started_at": front.started_at.isoformat(),
        "ended_at": front.ended_at.isoformat() if front.ended_at else None,
        "member_ids": [str(m.id) for m in front.members],
        "custom_status_encrypted": front.custom_status,
    }


def _audit_snapshot_to_read(snapshot: dict) -> FrontSnapshot:
    """Inverse of `_front_snapshot_for_audit` for the audit-list endpoint —
    decrypts custom_status the same way the live front read does."""
    ciphertext = snapshot.get("custom_status_encrypted")
    return FrontSnapshot(
        started_at=datetime.fromisoformat(snapshot["started_at"]),
        ended_at=(
            datetime.fromisoformat(snapshot["ended_at"])
            if snapshot.get("ended_at")
            else None
        ),
        member_ids=[uuid.UUID(m) for m in snapshot.get("member_ids", [])],
        custom_status=decrypt(ciphertext) if ciphertext else None,
    )


@router.patch(
    "/{front_id}",
    response_model=FrontRead,
    dependencies=[Depends(require_scope("fronts:write"))],
)
async def update_front(
    front_id: uuid.UUID,
    body: FrontUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(Front.id == front_id, Front.system_id == system.id)
    )
    front = result.scalar_one_or_none()
    if front is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Front not found")

    # Capture pre-edit state for both the audit log and the
    # currently-fronting notification emit.
    before_state = await snapshot_front_state(db, system.id)
    before_snapshot = _front_snapshot_for_audit(front)
    fronting_ids_at_edit = list(before_state.fronting_member_ids)

    fields_set = body.model_fields_set
    has_explicit_change = False

    if "started_at" in fields_set:
        if body.started_at is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="started_at cannot be null",
            )
        front.started_at = body.started_at
        has_explicit_change = True

    if "ended_at" in fields_set:
        # Explicit null reopens a closed front. A non-null value either
        # closes an open front or moves an existing close timestamp.
        front.ended_at = body.ended_at
        has_explicit_change = True

    if "custom_status" in fields_set:
        front.custom_status = (
            encrypt(body.custom_status) if body.custom_status else None
        )
        has_explicit_change = True

    if "member_ids" in fields_set and body.member_ids is not None:
        member_result = await db.execute(
            select(Member).where(
                Member.id.in_(body.member_ids),
                Member.system_id == system.id,
            )
        )
        members = list(member_result.scalars().all())
        if len(members) != len(body.member_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more member IDs are invalid",
            )
        front.members = members
        has_explicit_change = True

    # Sanity-check ordering. Allow overlap with adjacent entries (SP
    # parity), but reject the impossibilities.
    if front.ended_at is not None and front.ended_at < front.started_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ended_at cannot be earlier than started_at",
        )

    await db.flush()

    # Append the audit row only if something explicitly changed. A
    # no-op PATCH (empty body) doesn't pollute history.
    if has_explicit_change:
        after_snapshot = _front_snapshot_for_audit(front)
        if after_snapshot != before_snapshot:
            db.add(
                FrontAuditEvent(
                    id=uuid.uuid4(),
                    front_id=front.id,
                    actor_user_id=user.id,
                    fronting_member_ids=[str(m) for m in fronting_ids_at_edit],
                    before_snapshot=before_snapshot,
                    after_snapshot=after_snapshot,
                    created_at=datetime.now(UTC),
                )
            )

    after_state = await snapshot_front_state(db, system.id)
    await emit_front_change(
        db, system_id=system.id, before=before_state, after=after_state
    )

    await db.commit()
    await db.refresh(front, ["members"])
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.FRONT_DELETE
    )
    return _front_to_read(
        front,
        has_audit_history=await _front_has_audit(db, front.id),
        pending_delete_at=pending.get(front.id),
    )


@router.get(
    "/{front_id}/audit",
    response_model=list[FrontAuditEventRead],
)
async def list_front_audit(
    front_id: uuid.UUID,
    response: Response,
    # A front has no cap on the number of edits, and every audit row
    # carries two JSONB snapshots, so returning the whole log unbounded is
    # an O(edits) read. Page it. Default is generous enough that no real
    # UI hits the boundary; deeper callers follow the cursor. Constants are
    # inline (no config knob) - bump here if audit histories grow.
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
    """Return audit rows for a front entry, newest first.

    Gated by `fronts:read` (router-level dep). The audit log is
    system-internal; same caller can read the live entry, so the audit
    rows reveal nothing further. Hard-deleted with the entry via
    ON DELETE CASCADE on front_id.

    Keyset-paginated the same way `GET /v1/fronts` is: the body stays a
    plain array, and `X-Sheaf-Has-More` / `X-Sheaf-Next-Cursor` headers
    signal and drive the next page."""
    system = await _get_user_system(user, db)
    # Ownership check via the live front row - if the entry doesn't
    # belong to the caller's system, return 404 regardless of whether
    # audit rows happen to exist.
    front_result = await db.execute(
        select(Front).where(
            Front.id == front_id, Front.system_id == system.id
        )
    )
    if front_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Front not found"
        )

    query = (
        select(FrontAuditEvent)
        .where(FrontAuditEvent.front_id == front_id)
        # id is the deterministic tiebreaker for rows sharing a created_at,
        # matching the cursor's row comparison so pages neither skip nor
        # duplicate.
        .order_by(FrontAuditEvent.created_at.desc(), FrontAuditEvent.id.desc())
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
            tuple_(FrontAuditEvent.created_at, FrontAuditEvent.id)
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
        FrontAuditEventRead(
            id=row.id,
            front_id=row.front_id,
            actor_user_id=row.actor_user_id,
            fronting_member_ids=[uuid.UUID(s) for s in (row.fronting_member_ids or [])],
            before=_audit_snapshot_to_read(row.before_snapshot),
            after=_audit_snapshot_to_read(row.after_snapshot),
            created_at=row.created_at,
        )
        for row in page
    ]


@router.delete(
    "/{front_id}",
    dependencies=[Depends(require_scope("fronts:delete"))],
)
async def delete_front(
    front_id: uuid.UUID,
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
    result = await db.execute(
        select(Front).where(Front.id == front_id, Front.system_id == system.id)
    )
    front = result.scalar_one_or_none()
    if front is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Front not found")

    if is_safeguarded(system, PendingActionType.FRONT_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.FRONT_DELETE,
            target_id=front.id,
            target_label=f"Front starting {front.started_at.isoformat()}",
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

    await db.delete(front)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

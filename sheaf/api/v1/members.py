import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.api.deps import get_user_system as _get_user_system
from sheaf.auth.dependencies import get_current_user, has_scope, require_scope
from sheaf.crypto import blind_index, encrypt
from sheaf.database import get_db
from sheaf.encrypted_fields import (
    member_description_aad,
    member_name_aad,
    member_note_aad,
)
from sheaf.files import owned_avatar_url, owned_description_urls
from sheaf.middleware.rate_limit import write_rate_limit
from sheaf.models.content_revision import ContentRevision, ContentRevisionTarget
from sheaf.models.member import Member
from sheaf.models.pending_action import PendingActionType
from sheaf.models.security_event import SecurityEventType
from sheaf.models.share import ShareViewMember
from sheaf.models.system import PrivacyLevel, System
from sheaf.models.tag import Tag
from sheaf.models.user import User
from sheaf.observability.metrics import tier_label, tier_limit_hits_total
from sheaf.request import client_ip
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
from sheaf.services.analytics import score_recent_fronters_sql
from sheaf.services.journals import (
    capture_revision,
    decrypt_revision_for_read,
    delete_revisions_for,
    pin_revision,
    restore_member_bio_revision,
    unpin_revision_immediate,
)
from sheaf.services.member_defaults import default_fronting_private
from sheaf.services.member_limits import count_members, get_member_limit
from sheaf.services.members import decrypt_member_for_read, member_plaintext
from sheaf.services.memberships import group_ids_by_member, tag_ids_by_member
from sheaf.services.pagination import decode_cursor, encode_cursor
from sheaf.services.security_events import record_security_event
from sheaf.services.sharing import (
    exposure_activates_at,
    fronting_guard_release_exposes,
    member_privacy_raise_exposes,
    refuse_raise_when_publishing_unavailable,
    reject_mixed_exposure_directions,
    shared_view_memberships,
    stage_membership_exposure,
    view_serves_all_public_members,
    visibility_grace_days,
    visibility_step_up_required,
)
from sheaf.services.system_safety import (
    is_safeguarded,
    pending_finalize_after_by_target,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/members", tags=["members"])



async def _get_own_member(
    member_id: uuid.UUID,
    system: System,
    db: AsyncSession,
    *,
    for_update: bool = False,
) -> Member:
    stmt = select(Member).where(
        Member.id == member_id, Member.system_id == system.id
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
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


def _require_expansion_scope(request: Request, scope: str, param: str) -> None:
    """Refuse an expansion that reaches into a resource the caller cannot read.

    `/v1/members` is gated on `members:read`, but a member's groups and tags
    belong to resources behind `groups:read` and `tags:read`. Emitting them
    unasked would quietly turn a key scoped to members alone into one that
    also learns the group and tag structure.

    Refusing loudly rather than serving a thinner object is deliberate. A
    silently omitted field is indistinguishable from "this member is in no
    groups", and a client would cache that as truth; a 403 naming the missing
    scope tells the caller exactly what to fix. It also keeps the null in
    these fields meaning one thing only: you did not ask.

    Scope is decided by `has_scope`, the same helper the router-level
    dependency uses, so "write implies read" cannot drift between them.
    """
    if has_scope(request, scope):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Missing scope: {scope} (required by {param})",
    )


@router.get("", response_model=list[MemberRead])
async def list_members(
    request: Request,
    include_archived: bool = Query(
        default=True,
        description=(
            "Include archived members. Defaults to true: archived members are "
            "soft-hidden in the UI (lists / switcher) but must stay fetchable so "
            "historical surfaces (fronts, journals) can still resolve their names. "
            "Pass false for an active-only roster."
        ),
    ),
    include_group_ids: bool = Query(
        default=False,
        description=(
            "Include each member's group ids, so a client can build its "
            "member to groups map in one request. Requires the groups:read "
            "scope, which this endpoint does not otherwise need."
        ),
    ),
    include_tag_ids: bool = Query(
        default=False,
        description=(
            "Include each member's tag ids. Requires the tags:read scope."
        ),
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if include_group_ids:
        _require_expansion_scope(request, "groups:read", "include_group_ids")
    if include_tag_ids:
        _require_expansion_scope(request, "tags:read", "include_tag_ids")
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
            user.id,
            has_bio_revisions=m.id in with_revisions,
            pending_delete_at=pending.get(m.id),
        )
        for m in members
    ]
    # One query each for the whole roster, and only when asked. Empty list
    # rather than null for a member in nothing: null is "not asked for".
    if include_group_ids:
        groups_of = await group_ids_by_member(db, system.id)
        for m in decoded:
            m.group_ids = groups_of.get(m.id, [])
    if include_tag_ids:
        tags_of = await tag_ids_by_member(db, system.id)
        for m in decoded:
            m.tag_ids = tags_of.get(m.id, [])
    decoded.sort(key=lambda m: (m.display_name or m.name).casefold())
    return decoded


@router.post(
    "",
    response_model=MemberRead,
    status_code=status.HTTP_201_CREATED,
    # write_rate_limit(): shared per-account write budget (see fronts).
    dependencies=[Depends(require_scope("members:write")), write_rate_limit()],
)
async def create_member(
    body: MemberCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)

    # A member BORN public is the same exposure as raising one later, so it
    # meets the same publishing-availability gate `update_member` applies. The
    # guard's whole point is that a ceiling set while the instance's public
    # surface is off serves nobody today and would quietly START serving the
    # moment an operator flips the setting back; being new rather than edited
    # does not change that, and without this create was the way around it.
    born_staged = False
    born_exposed = False
    if body.privacy == PrivacyLevel.PUBLIC:
        refuse_raise_when_publishing_unavailable(user)
        # ...and the same STEP-UP and the same WAIT `update_member` applies,
        # whenever a view is set to serve every public member. A brand new
        # member is in no view, so for the whole life of this endpoint creating
        # one public exposed nobody; under that flag there is no view to be in
        # and the create IS the publication. Leaving it ungated would make
        # "delete them and add them back as public" the way around the slower
        # door - the same reason `group_raise_exposes` is asked by the group
        # CREATE path, and this is the same treatment that path gives: re-auth
        # now, then with a grace window set the member is born private with the
        # raise parked on `pending_privacy` for the finalizer, else born public.
        #
        # `view_serves_all_public_members` rather than
        # `member_privacy_raise_exposes`: there is no member id yet, and the
        # membership-row half of that answer is necessarily empty for a row that
        # does not exist.
        if visibility_step_up_required(system) and (
            await view_serves_all_public_members(db, system)
        ):
            await verify_destructive_auth(
                user, system, body.password, body.totp_code, db
            )
            born_exposed = True
            born_staged = visibility_grace_days(system) > 0

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
    # Step-up credentials are not member columns; drop them before anything
    # iterates the body, exactly as update_member does, so they can never be
    # passed to the Member constructor and persisted.
    data.pop("password", None)
    data.pop("totp_code", None)
    if born_staged:
        data["privacy"] = PrivacyLevel.PRIVATE
        data["pending_privacy"] = PrivacyLevel.PUBLIC
        data["privacy_activates_at"] = exposure_activates_at(system)
    # The one field whose default depends on another field in the same body.
    # Resolved through the shared helper so this endpoint and the importers
    # cannot disagree about what a brand new custom front is guarded with.
    data["fronting_private"] = default_fronting_private(
        is_custom_front=bool(data.get("is_custom_front")),
        requested=data.get("fronting_private"),
    )
    plaintext_name: str = data.pop("name")
    plaintext_description: str | None = data.pop("description", None)
    plaintext_note: str | None = data.pop("note", None)
    # Drop any avatar/banner/bio media that references another account's
    # storage keys before it reaches the DB - a foreign key would be
    # re-signed into a live serve URL on read (cross-tenant read oracle).
    data["avatar_url"] = owned_avatar_url(data.get("avatar_url"), user.id)
    data["banner_url"] = owned_avatar_url(data.get("banner_url"), user.id)
    plaintext_description = owned_description_urls(plaintext_description, user.id)
    # Pre-allocate the id (UUIDMixin's default only fires at flush) so the
    # encrypted cells can be bound to the row before it is inserted.
    member_id = uuid.uuid4()
    member = Member(
        id=member_id,
        system_id=system.id,
        name=encrypt(plaintext_name, aad=member_name_aad(member_id)),
        name_hash=blind_index(plaintext_name),
        description=(
            encrypt(plaintext_description, aad=member_description_aad(member_id))
            if plaintext_description is not None
            else None
        ),
        note=(
            encrypt(plaintext_note, aad=member_note_aad(member_id))
            if plaintext_note is not None and plaintext_note != ""
            else None
        ),
        **data,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    # A birth that publishes somebody is a raise like any other and leaves the
    # same IP/UA trail as `update_member` and `unarchive_member` do. Gated to
    # the exposing path: a member born public into no live all-public view was
    # never an exposure and records nothing, as before.
    if born_exposed:
        await record_security_event(
            event_type=SecurityEventType.EXPOSURE_RAISED,
            outcome="staged" if born_staged else "immediate",
            user_id=user.id,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
            detail={
                "source": "member_privacy",
                "member_id": str(member.id),
                "create": True,
            },
        )
    return decrypt_member_for_read(member, user.id)


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

    # Scored in the database rather than by loading the window's fronts.
    # This endpoint returns at most `limit` members, but the window is 180
    # days: a heavy switcher, or anyone who imported a long PluralKit
    # switch log, had every one of those fronts materialised as an ORM
    # object with its members eager-loaded on every request, and the cost
    # grew with their history forever. `score_recent_fronters` in
    # services/analytics.py is still the readable definition of the
    # scoring and the one to change first; a test pins the two together.
    scores = await score_recent_fronters_sql(
        db,
        system.id,
        now=now,
        since=since,
        half_life_days=_TOP_FRONTERS_HALF_LIFE_DAYS,
    )

    # Only the two columns the ordering needs, for the whole roster. The
    # full rows - encrypted bio and note included - are loaded afterwards
    # for the `limit` members that survive, not for everyone. Loading every
    # member to return eight was the whole cost of this endpoint on a large
    # system: the scoring query above runs in tens of milliseconds against
    # fifty thousand fronts, while a roster of a couple of thousand members
    # with long bios took seconds to hydrate on every quick-switch poll.
    rows = (
        await db.execute(
            select(Member.id, Member.quick_switch_pin).where(
                Member.system_id == system.id,
                Member.archived_at.is_(None),
            )
        )
    ).all()

    pinned = sorted(
        (r for r in rows if r.quick_switch_pin is not None),
        key=lambda r: (r.quick_switch_pin, str(r.id)),
    )
    # Highest score first; id as a stable tiebreaker for equal scores
    # (e.g. the long tail of members who haven't fronted in the window).
    unpinned = sorted(
        (r for r in rows if r.quick_switch_pin is None),
        key=lambda r: (-scores.get(r.id, 0.0), str(r.id)),
    )
    ordered_ids = [r.id for r in (pinned + unpinned)[:limit]]

    ordered: list[Member] = []
    if ordered_ids:
        full = (
            await db.execute(select(Member).where(Member.id.in_(ordered_ids)))
        ).scalars().all()
        by_id = {m.id: m for m in full}
        # Re-impose the ranking: IN () returns rows in whatever order the
        # planner likes.
        ordered = [by_id[i] for i in ordered_ids if i in by_id]

    with_revisions = await _load_bio_revision_existence(
        db, [m.id for m in ordered]
    )
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.MEMBER_DELETE
    )
    return [
        decrypt_member_for_read(
            m,
            user.id,
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
        user.id,
        has_bio_revisions=await _member_has_bio_revisions(db, member.id),
        pending_delete_at=pending.get(member.id),
    )


@router.patch(
    "/{member_id}",
    response_model=MemberRead,
    # A bio edit can capture a content revision, so it counts against the
    # shared per-account write budget too.
    dependencies=[Depends(require_scope("members:write")), write_rate_limit()],
)
async def update_member(
    member_id: uuid.UUID,
    body: MemberUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit a member, including the three settings that decide what strangers see.

    Those three - `privacy`, `fronting_private` and `never_shareable` - all
    obey the same asymmetry as the sharing endpoints: raising exposure is gated
    (step-up when the profile_visibility category is armed, and then a grace
    window for the two that have somewhere to be staged), lowering it is
    instant and ungated. `never_shareable` has no staging column, so its
    release is step-up-and-apply rather than step-up-and-wait; the two others
    stage behind `visibility_grace_days` when one is set.

    A single body may not carry BOTH a raise and a lowering: it is refused with
    400 before the step-up runs, so a failed re-auth can never take a lowering
    down with it. See `reject_mixed_exposure_directions`.
    """
    system = await _get_user_system(user, db)
    member = await _get_own_member(member_id, system, db, for_update=True)
    update_data = body.model_dump(exclude_unset=True)
    # Step-up credentials are not member columns; drop them before anything
    # iterates the update so they can never be persisted.
    password = update_data.pop("password", None)
    totp_code = update_data.pop("totp_code", None)

    # The three raise-to-public directions on this endpoint, captured before the
    # setattr loop touches anything: raising privacy to public, releasing the
    # fronting guard, and clearing never_shareable. Read off the requested body
    # and the member's CURRENT value, so they are the owner's intent regardless
    # of whether the profile_visibility category is armed or anything is actually
    # served. Used both for the publishing-availability gate just below and for
    # the audit record at the end.
    privacy_raise_requested = (
        update_data.get("privacy") == PrivacyLevel.PUBLIC
        and member.privacy != PrivacyLevel.PUBLIC
    )
    fronting_release_requested = (
        update_data.get("fronting_private") is False and member.fronting_private
    )
    never_shareable_release_requested = (
        update_data.get("never_shareable") is False and member.never_shareable
    )
    any_raise_requested = (
        privacy_raise_requested
        or fronting_release_requested
        or never_shareable_release_requested
    )

    # Any of the three raises is refused for the same reasons create_grant is:
    # not while the account is pending deletion, and not while the instance's
    # public surface is switched off. Publishing availability, not step-up, so it
    # fires whatever the safety category is set to. Lowering (private/friends,
    # re-arming a guard) stays open - going dark is never gated.
    if any_raise_requested:
        refuse_raise_when_publishing_unavailable(user)

    # Raising a member to `public` EXPOSES them, and there are two ways it can:
    # they are already sitting in a view something points at, or some live view
    # publishes every public member and this raise is itself the act of putting
    # them on it. `member_privacy_raise_exposes` is the single answer to both -
    # do NOT re-derive it from a membership-row lookup here, because a member
    # with no membership row is exactly the case the second path exists for,
    # and the row test alone reports "exposes nobody" about a raise that
    # publishes them.
    #
    # Same rule as the sharing endpoints - when the category is armed, re-auth
    # first; then, with a grace window set, the raise itself waits it out on the
    # member's own `pending_privacy` (staged below), and with no window it
    # applies at once because the re-auth already happened. The staging is the
    # same for both paths: the live ceiling is what every projection filters
    # on, so leaving it unmoved keeps the member off a curated roster and an
    # all-public one alike until the sweep promotes it.
    #
    # Lowering privacy is the un-exposing direction and stays instant and
    # ungated. private -> friends is ungated too because the friends tier is
    # parked and every grant that exists today is public-tier, so it exposes
    # nothing; when friends lands, this check has to become audience-aware
    # (a flip to `friends` would then expose to friend grants) alongside the
    # matching filter in share_projection._active_member_filter and the same
    # test in import_dedup._privacy_raise_exposes.
    privacy_raise_exposes = False
    if privacy_raise_requested and visibility_step_up_required(system):
        privacy_raise_exposes = await member_privacy_raise_exposes(
            db, system, member.id
        )

    # Releasing the dedicated fronting guard is another exposing direction.
    # Active and pending paths both count: this request receives a fresh full
    # grace window instead of piggybacking on an older pending action.
    fronting_release_exposes = False
    if (
        update_data.get("fronting_private") is False
        and member.fronting_private
        and visibility_step_up_required(system)
    ):
        fronting_release_exposes = await fronting_guard_release_exposes(
            db,
            system.id,
            member.id,
            member_is_public=member.privacy == PrivacyLevel.PUBLIC,
        )

    # Clearing `never_shareable` is the THIRD exposing direction here, and it
    # used to be the one that fell through to a plain setattr with no gate at
    # all - which made it the cheap way past the other two. It is the hardest
    # guard in the product ("this member appears in NO view, ever"), so
    # releasing it must not be easier than releasing the softer fronting guard
    # sitting right beside it in the same form.
    #
    # What it exposes used to be exactly what the fronting guard exposes.
    # Setting the flag deletes every `ShareViewMember` row for the member, so
    # releasing it could not put them back on a roster, in a group list, or at
    # the end of an edge - all of those composed out of those rows. What it
    # could do is let their presence leak through `project_fronting`, which
    # excludes a never-shareable member from the anonymous `hidden_count` as
    # well as from the naming, so a release while they are fronting turns
    # "nobody else is around" into "somebody else is".
    # `fronting_guard_release_exposes` is exactly that question, so it is the
    # same call, not a parallel one.
    #
    # `include_all_public_members` broke the first half of that. A view with
    # that flag needs no membership row, so for a member who is ALREADY public,
    # clearing never-shareable drops them straight onto a live roster - the
    # deleted rows protect nothing. So the same
    # `member_privacy_raise_exposes` the privacy raise asks is asked here too,
    # conditioned on the privacy this request leaves them at (the body may be
    # raising it in the same breath). Without this, the hardest guard in the
    # product would have been the cheapest of the three to release.
    #
    # Step-up alone is the gate: unlike `fronting_private` there is no
    # `never_shareable_activates_at` column to park the release in, and the
    # finalize sweep has nothing to promote, so with the category armed this
    # re-auths and applies immediately whatever the grace window is set to.
    # Adding a staging column is a migration and a sweep pass, and is worth
    # doing if this guard ever gates more than the presence bit; until then the
    # re-auth is the protection and the docstring says so rather than the code
    # implying a wait that does not happen.
    never_shareable_release_exposes = False
    if never_shareable_release_requested and visibility_step_up_required(system):
        # The privacy this request leaves them at, not the one they arrived
        # with: "clear never_shareable AND set public" is one body, and the
        # roster it lands them on is the one the new level reaches.
        ends_public = (
            update_data.get("privacy", member.privacy) == PrivacyLevel.PUBLIC
        )
        never_shareable_release_exposes = await fronting_guard_release_exposes(
            db,
            system.id,
            member.id,
            member_is_public=ends_public,
        )
        if not never_shareable_release_exposes and ends_public:
            never_shareable_release_exposes = await member_privacy_raise_exposes(
                db, system, member.id
            )

    # Whether any raise would ACTUALLY expose (category armed AND something to
    # serve). This is the step-up trigger, distinct from `any_raise_requested`
    # above, which is the owner's intent regardless of the category.
    raises_exposure = bool(
        privacy_raise_exposes
        or fronting_release_exposes
        or never_shareable_release_exposes
    )
    # A body that also takes something DOWN must not ride on the raise's gate:
    # if the step-up below failed, the lowering would fail with it. Checked
    # before that step-up runs, and refused outright - see the helper.
    reject_mixed_exposure_directions(
        raises=raises_exposure,
        lowers=(
            (
                update_data.get("privacy") is not None
                and update_data["privacy"] != PrivacyLevel.PUBLIC
                and member.privacy == PrivacyLevel.PUBLIC
            )
            or (
                update_data.get("fronting_private") is True
                and not member.fronting_private
            )
            or (
                update_data.get("never_shareable") is True
                and not member.never_shareable
            )
        ),
    )

    # Step-up fires whenever any of the three raises would actually expose.
    # Staging is the separate question of whether a grace window is configured:
    # with grace at 0 the raise applies immediately (ceiling moved, guard
    # released now), the re-auth having already run above. The never-shareable
    # release is never staged either way - it has nowhere to be staged. The
    # activation time is keyed on the two raises that actually park behind it,
    # so what this reports back is a wait something was really put behind
    # rather than one the owner is told about and does not get.
    if raises_exposure:
        await verify_destructive_auth(user, system, password, totp_code, db)

    grace = visibility_grace_days(system)
    stage = grace > 0
    visibility_activates_at = (
        exposure_activates_at(system)
        if (privacy_raise_exposes or fronting_release_exposes)
        else None
    )
    # Only keep the guard live pending the finalizer when there is a window to
    # wait out; otherwise it is released now (re-auth already happened).
    defer_fronting_release = fronting_release_exposes and stage

    # Same ownership guard as create: a key from another account must not be
    # stored (and later re-signed) here. Filter before the revision-capture
    # comparison so the stored and compared values match.
    if "avatar_url" in update_data:
        update_data["avatar_url"] = owned_avatar_url(update_data["avatar_url"], user.id)
    if "banner_url" in update_data:
        update_data["banner_url"] = owned_avatar_url(update_data["banner_url"], user.id)
    if "description" in update_data:
        update_data["description"] = owned_description_urls(
            update_data["description"], user.id
        )
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
            member.name = encrypt(value, aad=member_name_aad(member.id))
            member.name_hash = blind_index(value)
        elif key == "description":
            member.description = (
                encrypt(value, aad=member_description_aad(member.id))
                if value is not None
                else None
            )
        elif key == "note":
            # Empty string clears the column. Notes are deliberately
            # overwrite-only; no revision capture here.
            if value is None or value == "":
                member.note = None
            else:
                member.note = encrypt(value, aad=member_note_aad(member.id))
        elif key == "fronting_private":
            if defer_fronting_release:
                # Keep the guard live until the finalizer releases it.
                member.fronting_private_activates_at = visibility_activates_at
            else:
                member.fronting_private = value
                member.fronting_private_activates_at = None
        elif key == "privacy":
            if (
                value == PrivacyLevel.PUBLIC
                and privacy_raise_exposes
                and visibility_activates_at is not None
            ):
                # Staged, the way a group or a field is: the live ceiling stays
                # where it was and the raise parks on the member until the
                # finalizer promotes it. The projection keeps hiding them by
                # their live ceiling, so this holds for every kind of view,
                # curated or not - no membership rows have to be demoted.
                member.pending_privacy = PrivacyLevel.PUBLIC
                member.privacy_activates_at = visibility_activates_at
            else:
                # Immediate: lowering, an ungated raise, or a re-auth'd raise
                # with no grace window. A lowering also cancels any raise that
                # was staged - going dark always wins and never waits.
                member.privacy = value
                member.pending_privacy = None
                member.privacy_activates_at = None
        else:
            setattr(member, key, value)

    # Marking a member never-shareable must enforce it, not just remember it:
    # pull them out of every share view immediately. The projection query also
    # filters never_shareable, but leaving stale membership rows around would
    # be a footgun the first time that filter is ever loosened.
    if update_data.get("never_shareable") is True:
        await db.execute(
            delete(ShareViewMember).where(ShareViewMember.member_id == member.id)
        )

    # The staged raise lives on the member's own `pending_privacy` (set in the
    # loop above), not in demoted membership rows: the live ceiling is what
    # every projection filters on, so leaving it unmoved hides the member from
    # curated and all-public rosters alike until the finalize sweep promotes
    # it. The rows are left exactly as they were. With no window (grace 0) the
    # ceiling moved at once above and the member is exposed now - the re-auth
    # was the whole gate.

    await db.commit()
    await db.refresh(member)
    # A member raise widens who a live grant serves, so it leaves an IP/UA
    # trail. Every raise-to-public direction records exactly one event, whether
    # it was step-up'd and staged, applied at once, or landed immediately because
    # the category is disarmed - so the audit does not go dark exactly when
    # step-up is off. `staged` when the raise was parked behind the grace
    # window, `immediate` otherwise (grace 0, a guard release with nowhere
    # to stage, or the category off). One event with a boolean per axis, keyed on
    # the owner's requested direction; no member content, only the id and flags.
    # Only raises record; lowering a ceiling or re-arming a guard un-exposes and
    # stays silent.
    if any_raise_requested:
        await record_security_event(
            event_type=SecurityEventType.EXPOSURE_RAISED,
            outcome="staged" if visibility_activates_at is not None else "immediate",
            user_id=user.id,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
            detail={
                "source": "member_privacy",
                "member_id": str(member.id),
                "privacy_raise": privacy_raise_requested,
                "fronting_release": fronting_release_requested,
                "never_shareable_release": never_shareable_release_requested,
            },
        )
    return decrypt_member_for_read(
        member,
        user.id,
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
        user.id,
        has_bio_revisions=await _member_has_bio_revisions(db, member.id),
    )


@router.post(
    "/{member_id}/unarchive",
    response_model=MemberRead,
    dependencies=[Depends(require_scope("members:write"))],
)
async def unarchive_member(
    member_id: uuid.UUID,
    request: Request,
    body: MemberDeleteConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Restore an archived member, treating a return to a published view as a raise.

    Archiving takes a member off every public surface at once (see
    `share_projection._active_member_filter`), so undoing it is an EXPOSING
    act wherever a grant still points at a view they sit in - and this module's
    rule is that exposing waits. So an unarchive that would put somebody back
    in front of strangers goes through the same two controls as raising them to
    `public`: step-up when the `profile_visibility` category is armed, then, if
    a grace window is set, their `ShareViewMember` rows are demoted to PENDING
    (`stage_membership_exposure`) so the projection keeps hiding them until the
    finalize sweep promotes them. With grace at 0 the rows stay ACTIVE and the
    re-auth is the whole gate.

    What does NOT wait is the member row itself: `archived_at` is cleared
    immediately either way, so the owner gets them back in their own roster,
    switcher and pickers the moment they ask. Only the public surface serves
    the window. Deliberately: archive is the owner's private filing decision as
    well as a public one, and making them wait a week to see their own member
    again would be the safety feature punishing the person it protects.

    The member's own `privacy` is never touched here. A member who was public
    before archiving is still public after; the wait is carried entirely by the
    membership rows, so nothing about their configuration silently changes
    under them.

    Ungated and instant when nothing points at them - no live-or-pending grant
    over a view they belong to (or, for a public member, over a view that
    serves every public member) means unarchiving exposes nobody, and friction
    bought for nothing is friction the next person learns to click through. The
    all-public path re-auths but cannot stage, having no membership rows to
    demote; see `member_privacy_raise_exposes`.
    The optional body carries step-up credentials in the same shape the member
    PATCH and the archive endpoint take.
    """
    system = await _get_user_system(user, db)
    # Locked like the privacy raise: this reads `archived_at`, decides whether
    # the restore exposes anybody, and writes back, so two concurrent restores
    # must not both pass the gate on the same stale read.
    member = await _get_own_member(member_id, system, db, for_update=True)
    if member.archived_at is None:
        # Already active: nothing is being re-exposed, so nothing to gate.
        return decrypt_member_for_read(
            member,
            user.id,
            has_bio_revisions=await _member_has_bio_revisions(db, member.id),
        )

    # Two ways coming back exposes them, and the second has no rows behind it:
    # a view that serves every public member puts a public member back on the
    # page the instant `archived_at` clears, with nothing in
    # `share_view_members` to notice. Same single answer the privacy raise uses,
    # asked only for a member whose ceiling actually reaches that roster - a
    # private member unarchiving into an all-public view exposes nobody, and
    # friction bought for nothing is friction people learn to click through.
    exposing_rows: list[ShareViewMember] = []
    exposes = False
    if visibility_step_up_required(system):
        exposing_rows = await shared_view_memberships(db, system, member.id)
        exposes = (
            await member_privacy_raise_exposes(
                db, system, member.id, memberships=exposing_rows
            )
            if member.privacy == PrivacyLevel.PUBLIC
            else bool(exposing_rows)
        )
    if exposes:
        await verify_destructive_auth(
            user,
            system,
            body.password if body else None,
            body.totp_code if body else None,
            db,
        )

    member.archived_at = None
    # Keyed on the ROWS, not on `exposes`: staging a member means demoting their
    # membership rows, so a restore whose only exposure path is the all-public
    # flag has nothing to park and must not be reported as waiting.
    activates_at = exposure_activates_at(system) if exposing_rows else None
    stage_membership_exposure(exposing_rows, activates_at)
    await db.commit()
    await db.refresh(member)
    # Unarchiving a member who still sits in a live view puts them back in front
    # of strangers, which is a raise like flipping them to public, so it leaves
    # the same IP/UA trail. Gated to the exposing path (nothing points at them
    # means nothing to record); `staged` vs `immediate` follows the grace window.
    if exposes:
        await record_security_event(
            event_type=SecurityEventType.EXPOSURE_RAISED,
            outcome="staged" if activates_at is not None else "immediate",
            user_id=user.id,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
            detail={
                "source": "member_privacy",
                "member_id": str(member.id),
                "unarchive": True,
            },
        )
    result = decrypt_member_for_read(
        member,
        user.id,
        has_bio_revisions=await _member_has_bio_revisions(db, member.id),
    )
    # Tells the client whether the restore is live to strangers now or still
    # sitting behind the window, so it can say which without a second request.
    result.share_exposure_activates_at = activates_at
    return result


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
        ContentRevisionRead.model_validate(decrypt_revision_for_read(r, user.id))
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
        user.id,
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
    return ContentRevisionRead.model_validate(decrypt_revision_for_read(revision, user.id))


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
        revision=ContentRevisionRead.model_validate(decrypt_revision_for_read(revision, user.id)),
    )

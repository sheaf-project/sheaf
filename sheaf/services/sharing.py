"""Share views and grants: exposure lifecycle and resolution.

This module owns every decision about *when* something becomes visible. Two
rules run through all of it:

1. **Exposing waits, un-exposing is instant.** Creating a grant, adding a
   member/field to a view that is already shared, turning one of that view's
   exposure flags on, or flipping a member in a shared view to `public`
   privacy, are all loosenings: when the system has a grace period and the
   `profile_visibility` safety category on, the change lands PENDING and only
   the finalize sweep promotes it. Revoking, rotating, removing, and turning
   flags back off are always immediate and never gated - nothing may slow down
   going dark.
2. **Nothing is exposed implicitly.** `ShareViewMember` is the sole authority
   on who appears. Groups are a bulk picker that expands into explicit member
   rows (see `expand_group_into_view`), never a rule evaluated at read time,
   so adding someone to a group can never silently publish them.

`Member.never_shareable` is refused here at the point of adding, and refused
again in the projection query (sheaf/services/share_projection.py). Both, on
purpose: "we remembered not to add them" is not a guarantee.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from sheaf.config import settings
from sheaf.crypto import hash_share_token
from sheaf.models.group import Group
from sheaf.models.member import Member, group_members
from sheaf.models.relationship import MemberRelationship
from sheaf.models.share import (
    ShareGrant,
    ShareGrantStatus,
    ShareItemStatus,
    ShareSubjectType,
    ShareView,
    ShareViewField,
    ShareViewGroup,
    ShareViewMember,
)
from sheaf.models.system import PrivacyLevel, System
from sheaf.models.user import User

# Length of the raw link token. 32 bytes of urlsafe base64 is ~43 chars and
# comfortably beyond guessing, which matters because the token IS the
# authorisation for a link grant.
_TOKEN_BYTES = 32


# ---------------------------------------------------------------------------
# Attestation gate
# ---------------------------------------------------------------------------


def require_adult_attestation(user: User) -> None:
    """Refuse to create any grant until the account has self-declared 18+.

    Deliberately the ONLY thing this gate does. We do not collect a date of
    birth or an identity document: verifying age is itself a privacy harm for
    exactly the people Sheaf exists for. Self-declaration is the accepted
    tradeoff, gating the highest-risk surface and nothing else.
    """
    if user.adult_attested_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Confirm you are 18 or older before creating a share link or "
                "public profile."
            ),
        )


# ---------------------------------------------------------------------------
# Exposure lifecycle
# ---------------------------------------------------------------------------


# The per-view booleans that widen what a view shows. Named once so the update
# endpoint and the finalize sweep cannot drift apart over which flags carry a
# pending_* twin.
#
# `member_permalinks` is deliberately absent. It publishes nothing the roster
# does not already publish - it only gives already-shown members a stable
# address - so there is no exposure to wait out, and putting it here would
# demand step-up and a grace window for a change that reveals nobody. Flags in
# this tuple are the ones where flipping to True means a stranger can read
# something they could not read before.
EXPOSURE_FLAGS: tuple[str, ...] = (
    "include_bio",
    "include_fronting",
    "fronting_show_count",
    "include_relationships",
    "include_members",
    "include_groups",
)


def is_exposure_safeguarded(system: System) -> bool:
    """True when exposing something should wait out the grace window."""
    return (
        system.safety_grace_period_days > 0
        and system.safety_applies_to_profile_visibility
    )


def _activation(system: System, *, already_shared: bool) -> tuple[str, datetime | None]:
    """(status, activates_at) for a row that exposes something.

    Adding to a view that nothing points at yet exposes nothing, so it is
    immediate; the grace window is spent when the grant itself is created.
    """
    if not already_shared or not is_exposure_safeguarded(system):
        return ShareItemStatus.ACTIVE.value, None
    return (
        ShareItemStatus.PENDING.value,
        datetime.now(UTC) + timedelta(days=system.safety_grace_period_days),
    )


def promote_view_flags(view: ShareView) -> None:
    """Copy a view's staged flag flips onto its live flags, in place.

    Only flags with something staged move; the rest keep whatever the user set
    meanwhile. Caller decides that the window has elapsed and commits.
    """
    for flag in EXPOSURE_FLAGS:
        pending = getattr(view, f"pending_{flag}")
        if pending is not None:
            setattr(view, flag, pending)
            setattr(view, f"pending_{flag}", None)
    view.flags_activate_at = None


def grant_live_clause(*, include_pending: bool = True):
    """SQL predicate for a grant that exposes something, now or on its own.

    The one place "live" is defined, so the owner-side questions ("does this
    use up a grant slot?", "is this view shared?", "does raising this member to
    public expose them?") and the anonymous resolution cannot drift apart, in
    the same spirit as `share_projection._active_member_filter`. Three
    conditions, all of them in SQL:

    - not revoked - revocation is the panic button and is always immediate;
    - not past `expires_at` - an expired grant serves nobody, so it must not
      consume the grant cap, mark a view as shared, or drag a member privacy
      raise through step-up and a grace window;
    - PENDING counts alongside ACTIVE by default, because a pending grant goes
      live on its own: something added now must serve its own grace window
      rather than riding in on the grant's.

    `include_pending=False` narrows it to grants serving content *right now*,
    which is what the anonymous surface resolves against - inside its grace
    window a pending grant shows nobody anything.
    """
    now = datetime.now(UTC)
    statuses = [ShareGrantStatus.ACTIVE.value]
    if include_pending:
        statuses.append(ShareGrantStatus.PENDING.value)
    return (
        ShareGrant.status.in_(statuses)
        & ShareGrant.revoked_at.is_(None)
        & or_(ShareGrant.expires_at.is_(None), ShareGrant.expires_at > now)
    )


async def view_is_shared(db: AsyncSession, view_id: uuid.UUID) -> bool:
    """True if a live (or not-yet-live) grant points at this view."""
    result = await db.execute(
        select(ShareGrant.id).where(
            ShareGrant.view_id == view_id,
            grant_live_clause(),
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def shared_view_memberships(
    db: AsyncSession, system: System, member_id: uuid.UUID
) -> list[ShareViewMember]:
    """This member's rows in views something actually points at.

    The view-side equivalent of `view_is_shared`, from the member's end.
    """
    result = await db.execute(
        select(ShareViewMember)
        .join(ShareGrant, ShareGrant.view_id == ShareViewMember.view_id)
        .where(
            ShareViewMember.member_id == member_id,
            ShareGrant.system_id == system.id,
            grant_live_clause(),
        )
        .distinct()
    )
    return list(result.scalars().all())


async def fronting_guard_release_exposes(
    db: AsyncSession,
    system_id: uuid.UUID,
    member_id: uuid.UUID,
    *,
    member_is_public: bool,
) -> bool:
    """Whether an active/pending path can expose a released fronting guard.

    Include pending paths: a guard release requested near the end of an older
    grant/flag/membership window must still receive its own full grace period,
    not inherit only the older action's remaining time. A member outside the
    view can expose an anonymous presence bit when show-count is enabled.
    """
    eligible_membership = (
        (ShareViewMember.view_id == ShareView.id)
        & (ShareViewMember.member_id == member_id)
        & ShareViewMember.status.in_(
            [ShareItemStatus.ACTIVE.value, ShareItemStatus.PENDING.value]
        )
    )
    visibility_paths = [
        ShareView.fronting_show_count.is_(True),
        ShareView.pending_fronting_show_count.is_(True),
    ]
    if member_is_public:
        visibility_paths.append(ShareViewMember.id.is_not(None))

    result = await db.execute(
        select(ShareView.id)
        .join(ShareGrant, ShareGrant.view_id == ShareView.id)
        .outerjoin(ShareViewMember, eligible_membership)
        .where(
            ShareView.system_id == system_id,
            or_(
                ShareView.include_fronting.is_(True),
                ShareView.pending_include_fronting.is_(True),
            ),
            grant_live_clause(),
            or_(*visibility_paths),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def relationship_raise_exposes(
    db: AsyncSession,
    system: System,
    *,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
) -> bool:
    """Whether publishing this edge would actually put it in front of anybody.

    Takes the two endpoint ids rather than an edge row so the CREATE path can
    ask the same question about an edge that does not exist yet. Creating one
    already public and raising an existing one to public are the same exposure,
    and must not have different gates in front of them - otherwise "delete it
    and add it back as public" is a way around the slower door.

    The sibling of `fronting_guard_release_exposes`, for the other thing an
    owner can raise to `public`. An edge is the most relational data in Sheaf -
    it names two people at once - so it only projects when EVERY gate below is
    already open, and it is only worth a grace window when the same is true:

    - both endpoints are members of one view (the SAME view: an edge whose ends
      sit in two different views is never drawn), each with an active or
      pending row;
    - that view has `include_relationships` on, or staged on;
    - a grant points at that view and passes `grant_live_clause()`;
    - and both endpoints clear the member ceiling the projection applies -
      `privacy == public` and not `never_shareable`.

    Pending counts on every axis (membership rows, the view flag, the grant)
    for the reason it counts everywhere else in this module: a pending thing
    goes live on its own, so a raise requested near the end of someone else's
    window must serve its own full one rather than inheriting the remainder.

    A False here means the raise is instant, which is correct AND safe: with
    nothing pointing at the edge there is no exposure to delay, and if a view
    or grant appears later, that action carries its own gate and its own
    window.
    """
    endpoints = (
        (
            await db.execute(
                select(Member).where(
                    Member.id.in_([source_id, target_id]),
                    Member.system_id == system.id,
                )
            )
        )
        .scalars()
        .all()
    )
    # Two distinct rows expected; a self-edge cannot exist (CHECK constraint)
    # and a dangling endpoint cannot project.
    if len(endpoints) != 2:
        return False
    if any(
        m.never_shareable or m.privacy != PrivacyLevel.PUBLIC for m in endpoints
    ):
        return False

    source_row = aliased(ShareViewMember)
    target_row = aliased(ShareViewMember)
    eligible = [ShareItemStatus.ACTIVE.value, ShareItemStatus.PENDING.value]
    result = await db.execute(
        select(ShareView.id)
        .join(ShareGrant, ShareGrant.view_id == ShareView.id)
        .join(
            source_row,
            (source_row.view_id == ShareView.id)
            & (source_row.member_id == source_id),
        )
        .join(
            target_row,
            (target_row.view_id == ShareView.id)
            & (target_row.member_id == target_id),
        )
        .where(
            ShareView.system_id == system.id,
            or_(
                ShareView.include_relationships.is_(True),
                ShareView.pending_include_relationships.is_(True),
            ),
            source_row.status.in_(eligible),
            target_row.status.in_(eligible),
            grant_live_clause(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def relationship_exposed_member_ids(
    db: AsyncSession, system: System
) -> set[uuid.UUID]:
    """Members an edge could be published THROUGH right now.

    `relationship_raise_exposes` answers the question for one edge the owner is
    deliberately raising. This answers it in bulk, for the importer, which has
    no such deliberate act to hang a gate on: an edge arrives from a file
    already marked `public`, and if both of its endpoints are in here, storing
    that level as-is would publish the edge the instant the import commits -
    no step-up, no grace window, no screen the user had to look at.

    Deliberately coarser than the single-edge test: it does not require both
    endpoints to share ONE view, so two members published through two different
    views count. That direction of imprecision keeps an edge private that might
    have been safe to publish, which is the only direction worth being wrong in.
    """
    result = await db.execute(
        select(Member.id)
        .join(ShareViewMember, ShareViewMember.member_id == Member.id)
        .join(ShareView, ShareView.id == ShareViewMember.view_id)
        .join(ShareGrant, ShareGrant.view_id == ShareView.id)
        .where(
            Member.system_id == system.id,
            Member.never_shareable.is_(False),
            Member.privacy == PrivacyLevel.PUBLIC,
            ShareViewMember.status.in_(
                [ShareItemStatus.ACTIVE.value, ShareItemStatus.PENDING.value]
            ),
            or_(
                ShareView.include_relationships.is_(True),
                ShareView.pending_include_relationships.is_(True),
            ),
            grant_live_clause(),
        )
        .distinct()
    )
    return set(result.scalars().all())


async def group_raise_exposes(
    db: AsyncSession, system: System, group_id: uuid.UUID | None = None
) -> bool:
    """Whether publishing this group would actually put it in front of anybody.

    The third sibling of `fronting_guard_release_exposes` and
    `relationship_raise_exposes`, for the other thing an owner can raise to
    `public`. True when a view with `include_groups` on (or staged on) has a
    grant passing `grant_live_clause()`. Pending counts on both axes for the
    reason it counts everywhere in this module: a pending thing goes live on
    its own, so a raise requested near the end of someone else's window must
    serve its own full one rather than inheriting the remainder.

    Note the deliberate difference from the edge test above, which insists on
    BOTH endpoints being projected by the SAME view. A group has no endpoints
    to protect: the group itself is the payload - its name, description and
    colour, all of them things the owner wrote about the group. Its roster is
    not a second exposure decision, because `share_projection.project_groups`
    intersects it with the members that view already serves, so a public group
    can never name somebody the view was not already naming. There is
    therefore nothing here for a both-ends test to guard, and inventing one
    would only make the gate lie about what it protects.

    `group_id` is taken so a call site reads as a question about the group in
    hand, and it is OPTIONAL because the answer deliberately does not depend on
    it - which is exactly why the CREATE path (a group that does not exist yet)
    and the importer (asking once for a whole file) can both ask without one.
    Creating a group already public and raising an existing one are the same
    exposure and must not have different doors in front of them.

    A False means the raise is instant, which is correct AND safe: with no view
    serving groups there is nothing to delay, and if such a view or grant
    appears later, that action carries its own gate and its own window.
    """
    result = await db.execute(
        select(ShareView.id)
        .join(ShareGrant, ShareGrant.view_id == ShareView.id)
        .where(
            ShareView.system_id == system.id,
            or_(
                ShareView.include_groups.is_(True),
                ShareView.pending_include_groups.is_(True),
            ),
            grant_live_clause(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# View membership
# ---------------------------------------------------------------------------


async def add_member_to_view(
    *,
    db: AsyncSession,
    system: System,
    view: ShareView,
    member: Member,
    already_shared: bool | None = None,
) -> ShareViewMember | None:
    """Add one member to a view. Returns None if already present.

    Refuses `never_shareable` members outright - they are excluded from every
    view, with no override.
    """
    if member.never_shareable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{member.display_name or 'This member'} is marked never "
                "shareable and cannot be added to a shared view."
            ),
        )

    existing = await db.execute(
        select(ShareViewMember).where(
            ShareViewMember.view_id == view.id,
            ShareViewMember.member_id == member.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    if already_shared is None:
        already_shared = await view_is_shared(db, view.id)
    row_status, activates_at = _activation(system, already_shared=already_shared)

    row = ShareViewMember(
        id=uuid.uuid4(),
        view_id=view.id,
        member_id=member.id,
        status=row_status,
        activates_at=activates_at,
        created_at=datetime.now(UTC),
    )
    db.add(row)
    return row


async def expand_group_into_view(
    *,
    db: AsyncSession,
    system: System,
    view: ShareView,
    group_id: uuid.UUID,
) -> tuple[list[ShareViewMember], int, int]:
    """Populate a view from a group's CURRENT members.

    Returns (added_rows, skipped_never_shareable, skipped_not_public).

    This is a one-shot expansion into explicit member rows, not a live rule.
    The association is recorded in `ShareViewGroup` purely so the UI can show
    provenance and offer an explicit re-sync later. Group membership changing
    never moves anyone into or out of a view by itself, because that would
    publish someone with no deliberate step and no grace window.
    """
    result = await db.execute(
        select(Member)
        .join(group_members, group_members.c.member_id == Member.id)
        .where(
            group_members.c.group_id == group_id,
            Member.system_id == system.id,
        )
    )
    members = result.scalars().all()

    already_shared = await view_is_shared(db, view.id)
    added: list[ShareViewMember] = []
    skipped_secret = 0
    skipped_not_public = 0
    for member in members:
        if member.never_shareable:
            # Silently skipped rather than failing the whole expansion: a
            # secret member inside an otherwise shareable group is an ordinary
            # situation, not an error. The count is reported back to the user.
            skipped_secret += 1
            continue
        if member.privacy != PrivacyLevel.PUBLIC:
            # AUTO-inclusion respects the privacy ceiling: a private (the
            # default) or friends-only member is never swept into a view by a
            # group expansion. They would not project at the public tier anyway,
            # and pulling them in silently is exactly the accidental-inclusion
            # this feature guards against. A manual add is still allowed (a
            # deliberate act), but the bulk path stays conservative.
            skipped_not_public += 1
            continue
        row = await add_member_to_view(
            db=db,
            system=system,
            view=view,
            member=member,
            already_shared=already_shared,
        )
        if row is not None:
            added.append(row)

    now = datetime.now(UTC)
    existing_link = await db.execute(
        select(ShareViewGroup).where(
            ShareViewGroup.view_id == view.id,
            ShareViewGroup.group_id == group_id,
        )
    )
    link = existing_link.scalar_one_or_none()
    if link is None:
        db.add(
            ShareViewGroup(
                id=uuid.uuid4(),
                view_id=view.id,
                group_id=group_id,
                synced_at=now,
                created_at=now,
            )
        )
    else:
        link.synced_at = now

    return added, skipped_secret, skipped_not_public


async def add_field_to_view(
    *,
    db: AsyncSession,
    system: System,
    view: ShareView,
    field_id: uuid.UUID,
) -> ShareViewField | None:
    """Expose one custom-field definition through a view."""
    existing = await db.execute(
        select(ShareViewField).where(
            ShareViewField.view_id == view.id,
            ShareViewField.field_id == field_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    row_status, activates_at = _activation(
        system, already_shared=await view_is_shared(db, view.id)
    )
    row = ShareViewField(
        id=uuid.uuid4(),
        view_id=view.id,
        field_id=field_id,
        status=row_status,
        activates_at=activates_at,
        created_at=datetime.now(UTC),
    )
    db.add(row)
    return row


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------


async def create_grant(
    *,
    db: AsyncSession,
    system: System,
    user: User,
    view: ShareView,
    subject_type: ShareSubjectType,
    note: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[ShareGrant, str | None]:
    """Point a subject at a view. Returns (grant, raw_token_or_None).

    The raw token is returned exactly once, here. Only its keyed HMAC is
    stored, so a database dump yields no working links.
    """
    require_adult_attestation(user)

    live = (
        await db.execute(
            select(func.count(ShareGrant.id)).where(
                ShareGrant.system_id == system.id,
                grant_live_clause(),
            )
        )
    ).scalar_one()
    if live >= settings.share_grants_max:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Maximum {settings.share_grants_max} live share links and "
                "public profiles per system. Revoke one you no longer need "
                "first."
            ),
        )

    if subject_type is ShareSubjectType.PUBLIC:
        # "Public" is a single audience, so two competing public views would be
        # ambiguous. Enforced by a partial unique index too; checked here to
        # return a clean error instead of an IntegrityError. Deliberately NOT
        # `grant_live_clause()`: the index is `revoked_at IS NULL` and a partial
        # index cannot carry an expiry comparison, so relaxing this alone would
        # turn "republish over an expired public grant" into an IntegrityError.
        # Revoking the expired one first is the (already suggested) way through.
        existing = await db.execute(
            select(ShareGrant).where(
                ShareGrant.system_id == system.id,
                ShareGrant.subject_type == ShareSubjectType.PUBLIC.value,
                ShareGrant.revoked_at.is_(None),
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This system already has a public profile. Revoke it "
                    "before publishing a different view."
                ),
            )

    raw_token: str | None = None
    token_hash: str | None = None
    if subject_type is ShareSubjectType.LINK:
        raw_token = secrets.token_urlsafe(_TOKEN_BYTES)
        token_hash = hash_share_token(raw_token)

    if is_exposure_safeguarded(system):
        grant_status = ShareGrantStatus.PENDING.value
        activates_at = datetime.now(UTC) + timedelta(
            days=system.safety_grace_period_days
        )
    else:
        grant_status = ShareGrantStatus.ACTIVE.value
        activates_at = None

    grant = ShareGrant(
        id=uuid.uuid4(),
        system_id=system.id,
        view_id=view.id,
        subject_type=subject_type.value,
        token_hash=token_hash,
        note=note,
        status=grant_status,
        activates_at=activates_at,
        expires_at=expires_at,
        created_at=datetime.now(UTC),
        created_by_user_id=user.id,
    )
    db.add(grant)
    return grant, raw_token


def revoke_grant(grant: ShareGrant) -> None:
    """Kill a grant. Immediate, ungated, idempotent - the panic button."""
    if grant.revoked_at is None:
        grant.revoked_at = datetime.now(UTC)
    grant.status = ShareGrantStatus.REVOKED.value


def rotate_grant_token(grant: ShareGrant) -> str:
    """Issue a new link token; the previous URL stops working immediately.

    Not gated by the grace window: rotation is how someone cuts off a link
    that has spread further than intended, so it must take effect at once.
    The grant's live/pending state is untouched.
    """
    if grant.subject_type != ShareSubjectType.LINK.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only link grants have a token to rotate.",
        )
    raw_token = secrets.token_urlsafe(_TOKEN_BYTES)
    grant.token_hash = hash_share_token(raw_token)
    return raw_token


# ---------------------------------------------------------------------------
# Resolution (used by the anonymous public surface)
# ---------------------------------------------------------------------------


async def resolve_public_grant(
    db: AsyncSession, system_id: uuid.UUID
) -> tuple[ShareGrant, ShareView] | None:
    """Resolve a system's live public grant, or None.

    Returning a bare None for every failure mode is deliberate: the caller
    turns all of them into an identical 404 so there is no oracle telling an
    anonymous visitor whether a system exists, was never public, or just went
    dark.
    """
    result = await db.execute(
        select(ShareGrant, ShareView)
        .join(ShareView, ShareView.id == ShareGrant.view_id)
        .where(
            ShareGrant.system_id == system_id,
            ShareGrant.subject_type == ShareSubjectType.PUBLIC.value,
            grant_live_clause(include_pending=False),
        )
    )
    row = result.first()
    return (row[0], row[1]) if row else None


async def resolve_link_grant(
    db: AsyncSession, raw_token: str
) -> tuple[ShareGrant, ShareView] | None:
    """Resolve a link token to its live grant, or None.

    Lookup is by keyed hash, so the raw token is never compared against
    anything stored and never needs to exist in the database.
    """
    if not raw_token:
        return None
    result = await db.execute(
        select(ShareGrant, ShareView)
        .join(ShareView, ShareView.id == ShareGrant.view_id)
        .where(
            ShareGrant.token_hash == hash_share_token(raw_token),
            ShareGrant.subject_type == ShareSubjectType.LINK.value,
            grant_live_clause(include_pending=False),
        )
    )
    row = result.first()
    return (row[0], row[1]) if row else None


# ---------------------------------------------------------------------------
# Finalize sweep (promotes rows whose grace window has elapsed)
# ---------------------------------------------------------------------------


async def finalize_share_activations(db: AsyncSession) -> int:
    """Promote every pending grant, membership, field, and view flag flip whose
    activation time has passed.

    Mirrors the pending-action sweep. Returns the number of rows promoted.
    Revoked grants are skipped: revocation during the grace window means the
    exposure never happens at all.
    """
    now = datetime.now(UTC)
    promoted = 0

    grants = await db.execute(
        select(ShareGrant).where(
            ShareGrant.status == ShareGrantStatus.PENDING.value,
            ShareGrant.revoked_at.is_(None),
            ShareGrant.activates_at.is_not(None),
            ShareGrant.activates_at <= now,
        )
    )
    for grant in grants.scalars().all():
        grant.status = ShareGrantStatus.ACTIVE.value
        promoted += 1

    for model in (ShareViewMember, ShareViewField):
        rows = await db.execute(
            select(model).where(
                model.status == ShareItemStatus.PENDING.value,
                model.activates_at.is_not(None),
                model.activates_at <= now,
            )
        )
        for row in rows.scalars().all():
            row.status = ShareItemStatus.ACTIVE.value
            promoted += 1

    # Promote flags in one conditional UPDATE. If the owner cancels before
    # this statement, a NULL pending value preserves the live flag; if they
    # cancel afterward, their tightening is the last write. Going dark wins in
    # either ordering, without a stale ORM object resurrecting the flag.
    views = await db.execute(
        update(ShareView)
        .where(
            ShareView.flags_activate_at.is_not(None),
            ShareView.flags_activate_at <= now,
        )
        .values(
            include_bio=case(
                (ShareView.pending_include_bio.is_not(None),
                 ShareView.pending_include_bio),
                else_=ShareView.include_bio,
            ),
            include_fronting=case(
                (ShareView.pending_include_fronting.is_not(None),
                 ShareView.pending_include_fronting),
                else_=ShareView.include_fronting,
            ),
            fronting_show_count=case(
                (ShareView.pending_fronting_show_count.is_not(None),
                 ShareView.pending_fronting_show_count),
                else_=ShareView.fronting_show_count,
            ),
            include_relationships=case(
                (ShareView.pending_include_relationships.is_not(None),
                 ShareView.pending_include_relationships),
                else_=ShareView.include_relationships,
            ),
            include_members=case(
                (ShareView.pending_include_members.is_not(None),
                 ShareView.pending_include_members),
                else_=ShareView.include_members,
            ),
            include_groups=case(
                (ShareView.pending_include_groups.is_not(None),
                 ShareView.pending_include_groups),
                else_=ShareView.include_groups,
            ),
            pending_include_bio=None,
            pending_include_fronting=None,
            pending_fronting_show_count=None,
            pending_include_relationships=None,
            pending_include_members=None,
            pending_include_groups=None,
            flags_activate_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    promoted += views.rowcount or 0

    # The same atomic shape makes restoring the guard race-safe: a concurrent
    # tightening clears the timestamp and either wins the predicate or lands
    # last after it.
    member_guards = await db.execute(
        update(Member)
        .where(
            Member.fronting_private.is_(True),
            Member.fronting_private_activates_at.is_not(None),
            Member.fronting_private_activates_at <= now,
        )
        .values(fronting_private=False, fronting_private_activates_at=None)
        .execution_options(synchronize_session=False)
    )
    promoted += member_guards.rowcount or 0

    # Staged relationship-edge raises, same atomic shape for the same reason:
    # a lowering that lands mid-sweep clears `visibility_activates_at` and so
    # either falls outside the predicate or writes last. The `case()` guards
    # the ordering where the timestamp survived but the staged level did not -
    # then the live level is kept rather than nulled out.
    edge_raises = await db.execute(
        update(MemberRelationship)
        .where(
            MemberRelationship.visibility_activates_at.is_not(None),
            MemberRelationship.visibility_activates_at <= now,
        )
        .values(
            visibility=case(
                (MemberRelationship.pending_visibility.is_not(None),
                 MemberRelationship.pending_visibility),
                else_=MemberRelationship.visibility,
            ),
            pending_visibility=None,
            visibility_activates_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    promoted += edge_raises.rowcount or 0

    # Staged group raises, same atomic shape and the same reasoning as the
    # edges above: a lowering that lands mid-sweep clears
    # `privacy_activates_at` and so either falls outside the predicate or
    # writes last, and the `case()` covers the ordering where the timestamp
    # survived but the staged level did not.
    group_raises = await db.execute(
        update(Group)
        .where(
            Group.privacy_activates_at.is_not(None),
            Group.privacy_activates_at <= now,
        )
        .values(
            privacy=case(
                (Group.pending_privacy.is_not(None), Group.pending_privacy),
                else_=Group.privacy,
            ),
            pending_privacy=None,
            privacy_activates_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    promoted += group_raises.rowcount or 0

    return promoted
